"""评估 Runner（§11.3）：逐条回放数据集 → 捕获轨迹 → 确定性指标 + LLM Judge。

约定：
- 每条目使用独立线程 + InMemorySaver（评估不需要持久化 checkpoint）
- ask 权限在评估中自动放行（保持确定性；HITL 由生产 run 覆盖）
- scripted 模式（LIVE_LLM=0）时 judge 跳过，指标记 judge_skipped
"""

import time
import uuid
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import GraphContext, build_graph
from app.agent.runner import build_node_llms
from app.core.logging import get_logger
from app.db.models import Agent, EvalDataset, EvalRun, EvalScore
from app.db.session import SessionLocal
from app.eval.judge import judge_answer
from app.events.bus import EventBus
from app.tools.mcp_client import mcp_manager
from app.tools.registry import build_registry

logger = get_logger(__name__)


def _sequence_match(actual: list[str], expected: list[str]) -> dict:
    if not expected:
        return {"exact": None, "prefix": None}  # 无期望序列 → 不参与该项指标
    exact = actual == expected
    prefix = actual[: len(expected)] == expected or expected[: len(actual)] == actual
    return {"exact": exact, "prefix": prefix}


def _eval_policy(agent_row: Agent) -> dict:
    """评估路径的权限策略：ask 一律视为 allow（自动放行），保持确定性。"""
    policy = agent_row.tool_policy or {}
    rules = [r for r in policy.get("rules", []) if r.get("action") != "ask"]
    default = policy.get("default", "allow")
    return {"rules": rules, "default": "allow" if default == "ask" else default}


async def _run_single(entry: dict, agent_row: Agent, llms: dict) -> dict:
    """执行单条：返回轨迹与结果（状态/答案/工具序列/耗时/用量/计划/反思）。"""
    thread_id = uuid.uuid4()
    bus = EventBus(uuid.uuid4(), persist=False)
    q = bus.subscribe()  # 运行期间收集事件（persist=False 时事件仅走订阅通道）
    registry = build_registry(thread_id)
    for name, meta in mcp_manager.tools().items():
        registry.register(name, meta)
    ctx = GraphContext(
        llms=llms,
        registry=registry,
        system_prompt=agent_row.system_prompt or "",
        planner_prompt=agent_row.planner_prompt or "",
        bus=bus,
        run_id=uuid.uuid4(),
        thread_id=thread_id,
        session=None,
        tool_policy=_eval_policy(agent_row),
    )
    graph = build_graph(ctx, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": str(thread_id)}}
    started = time.perf_counter()
    try:
        final = None
        async for _mode, payload in graph.astream(
            {"messages": [HumanMessage(content=entry["input"])]},
            config=config,
            stream_mode=["values"],
        ):
            final = payload
        assert final is not None
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        return {
            "status": "completed",
            "final_answer": final.get("final_answer") or "",
            "tool_sequence": [e.payload["name"] for e in events if e.type == "tool_call_start"],
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "usage": final.get("usage_total") or {},
            "iterations": final.get("iteration_count", 0),
            "plan": final.get("plan") or [],
            "reflection_log": final.get("reflection_log") or [],
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("eval_entry_failed", input=entry["input"][:50], error=str(exc)[:200])
        return {
            "status": "failed",
            "final_answer": "",
            "tool_sequence": [],
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "usage": {},
            "iterations": 0,
            "plan": [],
            "reflection_log": [],
            "error": str(exc)[:500],
        }
    finally:
        bus.unsubscribe(q)
        await bus.close()


async def run_eval(eval_run_id: uuid.UUID) -> None:
    """评估主流程：逐条执行 → 指标聚合 → Judge 评分 → 落库。"""
    async with SessionLocal() as session:
        erun = await session.get(EvalRun, eval_run_id)
        if erun is None:
            return
        dataset = await session.get(EvalDataset, erun.dataset_id)
        agent = await session.get(Agent, erun.agent_id) if erun.agent_id else None
        if dataset is None or agent is None:
            erun.status = "failed"
            erun.metrics = {"error": "dataset 或 agent 不存在"}
            erun.finished_at = datetime.now(UTC)
            await session.commit()
            return
        entries = dataset.entries or []
        llms = await build_node_llms(agent)
        erun.status = "running"
        erun.model_snapshot = {
            "agent": agent.name,
            "node_models": agent.node_models or {},
            "scripted": "scripted" in type(llms["executor"]).__name__.lower(),
        }
        await session.commit()

        scores: list[dict] = []
        for index, entry in enumerate(entries):
            result = await _run_single(entry, agent, llms)
            tool_match = _sequence_match(
                result["tool_sequence"], entry.get("expected_tool_calls") or []
            )
            judge = None
            if result["status"] == "completed" and result["final_answer"]:
                judge = await judge_answer(
                    llms["judge"],
                    question=entry["input"],
                    reference=entry.get("reference_answer"),
                    rubric=entry.get("rubric"),
                    actual=result["final_answer"],
                )
            scores.append(
                {
                    "entry_index": index,
                    "input": entry["input"],
                    "trajectory": {
                        "tool_sequence": result["tool_sequence"],
                        "iterations": result["iterations"],
                        "latency_ms": result["latency_ms"],
                        "usage": result["usage"],
                        "plan": result["plan"],
                        "reflection_log": result["reflection_log"],
                    },
                    "tool_seq_match": tool_match,
                    "answer_score": judge.score if judge else None,
                    "judge_reason": judge.reason if judge else None,
                    "error": result["error"],
                    "final_answer": result["final_answer"],
                }
            )

    metrics = _aggregate(scores)

    async with SessionLocal() as session:
        erun = await session.get(EvalRun, eval_run_id)
        erun.status = "completed"
        erun.metrics = metrics
        erun.finished_at = datetime.now(UTC)
        for s in scores:
            session.add(
                EvalScore(
                    eval_run_id=eval_run_id,
                    entry_index=s["entry_index"],
                    input=s["input"],
                    trajectory=s["trajectory"],
                    tool_seq_match=s["tool_seq_match"],
                    answer_score=s["answer_score"],
                    judge_reason=s["judge_reason"],
                    error=s["error"],
                )
            )
        await session.commit()
    logger.info("eval_completed", eval_run_id=str(eval_run_id), metrics=metrics)


def _aggregate(scores: list[dict]) -> dict:
    total = len(scores)
    completed = [s for s in scores if s["error"] is None]
    failed = total - len(completed)
    exact = [s for s in scores if s["tool_seq_match"]["exact"] is not None]
    judged = [s for s in scores if s["answer_score"] is not None]
    retried_steps = 0
    fixed_steps = 0
    for s in scores:
        for step in s["trajectory"]["plan"]:
            if step.get("attempts", 0) > 0:
                retried_steps += 1
                if step.get("status") == "done":
                    fixed_steps += 1

    def avg(seq, key=None) -> float | None:
        if not seq:
            return None
        values = [key(x) for x in seq] if key else seq
        return round(sum(values) / len(values), 2)

    return {
        "total": total,
        "completed": len(completed),
        "failed": failed,
        "success_rate": round(len(completed) / total, 4) if total else None,
        "avg_latency_ms": avg([s["trajectory"]["latency_ms"] for s in scores]),
        "avg_iterations": avg([s["trajectory"]["iterations"] for s in scores]),
        "avg_input_tokens": avg(scores, lambda s: s["trajectory"]["usage"].get("input_tokens", 0)),
        "avg_output_tokens": avg(
            scores, lambda s: s["trajectory"]["usage"].get("output_tokens", 0)
        ),
        "tool_seq_exact_rate": round(
            sum(1 for s in exact if s["tool_seq_match"]["exact"]) / len(exact), 4
        )
        if exact
        else None,
        "tool_seq_prefix_rate": round(
            sum(1 for s in exact if s["tool_seq_match"]["prefix"]) / len(exact), 4
        )
        if exact
        else None,
        "avg_answer_score": avg(judged, lambda s: s["answer_score"]),
        "judged": len(judged),
        "judge_skipped": total - len(judged),
        "reflection_fix_rate": round(fixed_steps / retried_steps, 4) if retried_steps else None,
    }

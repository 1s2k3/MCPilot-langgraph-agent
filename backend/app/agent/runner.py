"""运行管理：后台任务执行图、事件总线注册、SSE 回放、取消、checkpoint 接线。"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage
from langgraph.types import Command
from sqlalchemy import func, select

from app.agent.checkpoint import get_checkpointer
from app.agent.graph import GraphContext, build_graph
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.models import Agent, Message, Run
from app.db.models import Event as EventRow
from app.db.session import SessionLocal
from app.events.bus import EventBus
from app.events.models import Event
from app.llm.anthropic import build_anthropic_model
from app.llm.base import resolve_llm_config
from app.llm.scripted import (
    ScriptedChatModel,
    demo_finalizer_responder,
    demo_plan_responder,
    demo_reflect_responder,
    demo_responder,
)
from app.memory.extractor import store_extracted_memories
from app.security.keys import get_provider_key
from app.tools.executor import mask_secrets
from app.tools.mcp_client import mcp_manager
from app.tools.registry import build_registry

logger = get_logger(__name__)

_TERMINAL = ("completed", "failed", "cancelled")
_LLM_NODES = ("planner", "executor", "reflector", "finalizer", "memory_extractor", "judge")


async def build_node_llms(agent_row: Agent) -> tuple[dict[str, object], object | None]:
    """按节点构建 LLM 实例。scripted 模式每个节点独立实例（避免脚本队列串扰）。

    返回 (llms, structured_fn)：structured_fn 是 provider 相关的结构化输出适配；
    scripted 模式为 None（ScriptedChatModel 自带实现）。

    API Key 解析顺序（§9）：环境变量 → DB 密钥（api_keys 表）→ 降级 scripted。
    """
    s = get_settings()
    if s.scripted_llm:
        return _scripted_llms(), None
    overrides = lambda node: (agent_row.node_models or {}).get(node)  # noqa: E731

    if s.llm_provider == "deepseek":
        from app.llm.deepseek import build_deepseek_model
        from app.llm.deepseek import structured as ds_structured

        key = s.deepseek_api_key.get_secret_value() if s.deepseek_api_key else None
        if key is None:
            key = await get_provider_key("deepseek")
        if key is None:
            logger.warning("no_deepseek_key_using_scripted")
            return _scripted_llms(), None
        return {
            node: build_deepseek_model(resolve_llm_config(node, overrides(node)), api_key=key)
            for node in _LLM_NODES
        }, ds_structured

    from app.llm.anthropic import structured as an_structured

    key = s.anthropic_api_key.get_secret_value() if s.anthropic_api_key else None
    if key is None:
        key = await get_provider_key("anthropic")
    if key is None:
        logger.warning("no_anthropic_key_using_scripted")
        return _scripted_llms(), None
    return {
        node: build_anthropic_model(resolve_llm_config(node, overrides(node)), api_key=key)
        for node in _LLM_NODES
    }, an_structured


def _scripted_llms() -> dict[str, object]:
    return {
        "executor": ScriptedChatModel(responder=demo_responder),
        "planner": ScriptedChatModel(responder=demo_plan_responder),
        "reflector": ScriptedChatModel(responder=demo_reflect_responder),
        "finalizer": ScriptedChatModel(responder=demo_finalizer_responder),
        "memory_extractor": ScriptedChatModel(),
        "judge": ScriptedChatModel(),
    }


def sanitize_state(values: dict) -> dict:
    """state_snapshot 事件用脱敏视图：消息截断、工具参数保留但体积受限。"""
    out: dict = {}
    for key, value in values.items():
        if key == "messages":
            out["messages"] = [_msg_brief(m) for m in value]
        elif key == "summary":
            out["summary"] = (value or "")[:500]
        elif key == "memory_context":
            out["memory_context"] = (value or "")[:1000]
        else:
            out[key] = value
    return out


def _msg_brief(m) -> dict:
    content = m.content
    brief: dict = {"role": m.type}
    if isinstance(content, str):
        brief["content"] = content[:2000]
    else:
        brief["content"] = str(content)[:500]
    if getattr(m, "tool_calls", None):
        # state_snapshot 事件与 checkpoint 快照：工具参数同样脱敏（安全评审发现 3）
        brief["tool_calls"] = [
            {"name": tc.get("name"), "args": mask_secrets(tc.get("args"))} for tc in m.tool_calls
        ]
    return brief


async def _write_message(
    session, thread_id: uuid.UUID, run_id: uuid.UUID, role: str, content: str
) -> None:
    seq = (
        await session.execute(
            select(func.coalesce(func.max(Message.seq), 0)).where(Message.thread_id == thread_id)
        )
    ).scalar_one() + 1
    session.add(Message(thread_id=thread_id, run_id=run_id, role=role, content=content, seq=seq))


async def _fail_run(
    run: Run, bus: EventBus, *, code: str, message: str, exc: Exception | None = None
) -> None:
    now = datetime.now(UTC)
    run.status = "failed"
    run.error = {"code": code, "message": message}
    run.finished_at = now
    await bus.publish(bus.next_event("error", {"code": code, "message": message, "terminal": True}))
    await bus.publish(bus.next_event("run_end", {"status": "failed", "error": run.error}))


async def _post_run_memory_extraction(
    run_id: uuid.UUID, thread_id: uuid.UUID, llm, user_input: str, answer: str
) -> None:
    """运行结束后的异步长期记忆提取（best-effort，不发布事件、不影响 run 状态）。"""
    try:
        conversation = f"用户: {user_input}\n助手: {answer[:2000]}"
        async with asyncio.timeout(30):
            written = await store_extracted_memories(thread_id, run_id, llm, conversation)
        if written:
            logger.info("memory_extracted", run_id=str(run_id), written=written)
    except Exception:  # noqa: BLE001
        logger.warning("memory_extraction_task_failed", run_id=str(run_id))


async def execute_run(
    run_id: uuid.UUID,
    thread_id: uuid.UUID,
    agent: Agent,
    user_input: str,
    bus: EventBus,
) -> None:
    """一次运行的主流程（后台任务）。"""
    s = get_settings()
    started = time.perf_counter()
    budgets = {
        "max_llm_calls": s.max_llm_calls,
        "max_total_tool_calls": s.max_total_tool_calls,
        **(agent.budgets or {}),
    }
    registry = build_registry(thread_id)
    for name, meta in mcp_manager.tools().items():  # 注入 MCP 工具（带 server 前缀）
        registry.register(name, meta)
    llms, structured_fn = await build_node_llms(agent)
    checkpointer = await get_checkpointer()  # None = 数据库不可达（降级，无持久化）

    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        if run is None:
            logger.error("run_row_missing", run_id=str(run_id))
            return
        try:
            await bus.publish(
                bus.next_event(
                    "run_start",
                    {
                        "thread_id": str(thread_id),
                        "agent_id": str(agent.id),
                        "agent": agent.name,
                        "input": user_input,
                    },
                )
            )
            run.status = "running"
            await session.commit()

            ctx = GraphContext(
                llms=llms,
                registry=registry,
                system_prompt=agent.system_prompt or "",
                planner_prompt=agent.planner_prompt or "",
                bus=bus,
                run_id=run_id,
                thread_id=thread_id,
                budgets=budgets,
                session=session,
                window=s.short_term_window,
                tool_policy=agent.tool_policy or {},
                structured_fn=structured_fn,
            )
            graph = build_graph(ctx, checkpointer=checkpointer)
            config = {"configurable": {"thread_id": str(thread_id)}}
            # HITL 循环：interrupt 挂起 → 等待 resume → Command(resume=...) 继续
            # LangGraph 1.x：interrupt 以 __interrupt__ 更新项流式返回（不抛异常）
            stream_input: object = {"messages": [HumanMessage(content=user_input)]}
            while True:
                interrupted_value = None
                async with asyncio.timeout(s.run_timeout_seconds):
                    # updates 模式：每个节点结束产出 {node: state} → state_snapshot 事件
                    async for mode, payload in graph.astream(
                        stream_input, config=config, stream_mode=["updates"]
                    ):
                        if mode != "updates":
                            continue
                        if "__interrupt__" in payload:
                            raw = payload["__interrupt__"]
                            inter = raw[0] if isinstance(raw, (tuple, list)) else raw
                            interrupted_value = inter.value if hasattr(inter, "value") else inter
                            continue
                        for node_name, values in payload.items():
                            await bus.publish(
                                bus.next_event(
                                    "state_snapshot",
                                    {"node": node_name, "state": sanitize_state(values)},
                                )
                            )
                if interrupted_value is None:
                    break
                run.status = "interrupted"
                await session.commit()
                await bus.publish(
                    bus.next_event(
                        "interrupt",
                        {
                            "pending": (interrupted_value or {}).get("pending", []),
                            "resumable": True,
                        },
                    )
                )
                decision = await manager.wait_for_resume(run_id)  # 取消时抛 CancelledError
                run.status = "running"
                await session.commit()
                await bus.publish(
                    bus.next_event("resumed", {"action": (decision or {}).get("action")})
                )
                stream_input = Command(resume=decision)

            final_state = await graph.aget_state(config)
            values = final_state.values or {}
            answer = values.get("final_answer")
            if not answer:
                msgs = values.get("messages") or []
                last = msgs[-1] if msgs else None
                answer = last.content if last is not None and isinstance(last.content, str) else ""
            run.status = "completed"
            run.final_answer = answer
            run.latency_ms = int((time.perf_counter() - started) * 1000)
            run.usage = values.get("usage_total") or {}
            run.finished_at = datetime.now(UTC)
            await _write_message(session, thread_id, run_id, "assistant", answer)
            await session.commit()
            await bus.publish(
                bus.next_event(
                    "run_end",
                    {
                        "status": "completed",
                        "final_answer": answer,
                        "latency_ms": run.latency_ms,
                        "usage": run.usage,
                    },
                )
            )
            # 异步长期记忆提取（不阻塞 run 收尾，异常隔离）
            def _memory_task_done(task: asyncio.Task) -> None:
                try:
                    task.result()
                except Exception:  # noqa: BLE001
                    logger.warning("memory_extraction_bg_task_failed", run_id=str(run_id))

            bg_task = asyncio.create_task(
                _post_run_memory_extraction(
                    run_id, thread_id, llms["memory_extractor"], user_input, answer
                )
            )
            bg_task.add_done_callback(_memory_task_done)
        except asyncio.CancelledError:
            run.status = "cancelled"
            run.finished_at = datetime.now(UTC)
            await session.commit()
            try:
                await bus.publish(bus.next_event("run_end", {"status": "cancelled"}))
            except Exception:  # noqa: BLE001
                pass
            raise
        except TimeoutError:
            await _fail_run(
                run, bus, code="run_timeout", message=f"运行超时（{s.run_timeout_seconds}s）"
            )
            await session.commit()
        except AppError as exc:
            await _fail_run(run, bus, code=exc.code, message=exc.message)
            await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("run_failed", run_id=str(run_id))
            # 客户端/落库只存固定文案，原始异常仅进服务端日志（安全评审发现 6）
            await _fail_run(run, bus, code="internal_error", message="内部错误（详见服务端日志）")
            await session.commit()


class RunManager:
    """运行实例注册表：事件总线 + 后台任务生命周期 + HITL 决议通道。"""

    def __init__(self) -> None:
        self._buses: dict[uuid.UUID, EventBus] = {}
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._resume_futures: dict[uuid.UUID, asyncio.Future] = {}

    def bus_for(self, run_id: uuid.UUID) -> EventBus:
        if run_id not in self._buses:
            self._buses[run_id] = EventBus(run_id)
        return self._buses[run_id]

    def existing_bus(self, run_id: uuid.UUID) -> EventBus | None:
        return self._buses.get(run_id)

    def start(self, run_id: uuid.UUID, coro: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.create_task(self._run_and_cleanup(run_id, coro), name=f"run-{run_id}")
        self._tasks[run_id] = task

    def wait_for_resume(self, run_id: uuid.UUID) -> asyncio.Future:
        """interrupt 挂起后等待人工决议的 Future（resume API 填充结果）。"""
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._resume_futures[run_id] = fut
        return fut

    def resume(self, run_id: uuid.UUID, decision: dict) -> bool:
        fut = self._resume_futures.get(run_id)
        if fut is None or fut.done():
            return False
        fut.set_result(decision)
        return True

    async def _run_and_cleanup(
        self, run_id: uuid.UUID, coro: Callable[[], Awaitable[None]]
    ) -> None:
        try:
            await coro()
        except asyncio.CancelledError:
            pass  # execute_run 内部已处理状态
        finally:
            bus = self._buses.pop(run_id, None)
            if bus is not None:
                await bus.close()
            self._tasks.pop(run_id, None)
            self._resume_futures.pop(run_id, None)

    def cancel(self, run_id: uuid.UUID) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        fut = self._resume_futures.pop(run_id, None)
        if fut is not None and not fut.done():
            fut.cancel()  # 唤醒等待决议的任务 → CancelledError → 状态 cancelled
        task.cancel()
        return True

    async def replay(self, run_id: uuid.UUID, after_seq: int) -> list[Event]:
        """从 events 表回放（SSE 断线补拉 / Timeline 数据源）。"""
        async with SessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(EventRow)
                        .where(EventRow.run_id == run_id, EventRow.seq > after_seq)
                        .order_by(EventRow.seq)
                    )
                )
                .scalars()
                .all()
            )
        return [
            Event(
                seq=r.seq,
                run_id=str(r.run_id),
                type=r.type,
                payload=r.payload or {},
                ts=r.ts.isoformat(),
            )
            for r in rows
        ]


manager = RunManager()

"""M4 图拓扑单元测试：ScriptedLLM 确定性驱动 planner→executor→reflector→finalizer 全流程。

无 DB 依赖：load_context 数据库不可用时静默降级，工具不参与这些用例。
"""

import uuid

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import GraphContext, build_graph
from app.agent.schemas import Plan, PlanStep, ReflectionVerdict
from app.events.bus import EventBus
from app.llm.scripted import ScriptedChatModel
from app.tools.registry import build_registry


def _ctx(planner, executor, reflector, finalizer, bus) -> GraphContext:
    return GraphContext(
        llms={
            "planner": planner,
            "executor": executor,
            "reflector": reflector,
            "finalizer": finalizer,
        },
        registry=build_registry(uuid.uuid4()),
        system_prompt="测试助手",
        bus=bus,
        run_id=uuid.uuid4(),
        thread_id=uuid.uuid4(),
    )


async def _run_graph(ctx: GraphContext, user_input: str) -> tuple[dict, list[str]]:
    graph = build_graph(ctx)
    q = ctx.bus.subscribe()
    final = None
    async for chunk in graph.astream(
        {"messages": [HumanMessage(content=user_input)]}, stream_mode=["values"]
    ):
        _, final = chunk  # LangGraph 1.x: (mode, payload)
    types = []
    while not q.empty():
        types.append(q.get_nowait().type)
    await ctx.bus.close()
    assert final is not None
    return final, types


async def test_happy_path_single_step() -> None:
    bus = EventBus(uuid.uuid4(), persist=False)
    ctx = _ctx(
        ScriptedChatModel([Plan(steps=[PlanStep(id="s1", goal="第一步")], rationale="r")]),
        ScriptedChatModel([AIMessage(content="步骤完成")]),
        ScriptedChatModel([ReflectionVerdict(verdict="pass", reason="ok")]),
        ScriptedChatModel([AIMessage(content="最终答案")]),
        bus,
    )
    final, types = await _run_graph(ctx, "任务")
    assert final["final_answer"] == "最终答案"
    assert final["plan"][0]["status"] == "done"
    assert len(final["reflection_log"]) == 1
    for t in ("plan_created", "step_start", "step_done", "reflect"):
        assert t in types, types


async def test_retry_with_feedback_injection_then_pass() -> None:
    bus = EventBus(uuid.uuid4(), persist=False)
    executor = ScriptedChatModel(
        [AIMessage(content="第一次（不完整）"), AIMessage(content="第二次（已修正）")]
    )
    reflector = ScriptedChatModel(
        [
            ReflectionVerdict(verdict="retry", feedback="请补充细节"),
            ReflectionVerdict(verdict="pass", reason="ok"),
        ]
    )
    ctx = _ctx(
        ScriptedChatModel([Plan(steps=[PlanStep(id="s1", goal="第一步")], rationale="")]),
        executor,
        reflector,
        ScriptedChatModel([AIMessage(content="最终答案")]),
        bus,
    )
    final, types = await _run_graph(ctx, "任务")
    step = final["plan"][0]
    assert step["status"] == "done"
    assert step["attempts"] == 1
    assert step["feedback"] == ["请补充细节"]
    assert len(final["reflection_log"]) == 2
    # 反馈确实注入到了 executor 的第二次调用
    assert any("请补充细节" in str(m) for m in executor.calls[1].messages)


async def test_retry_exhausted_triggers_replan() -> None:
    bus = EventBus(uuid.uuid4(), persist=False)
    planner = ScriptedChatModel(
        [
            Plan(steps=[PlanStep(id="s1", goal="原计划步骤")], rationale=""),
            Plan(steps=[PlanStep(id="s2", goal="新计划步骤")], rationale=""),
        ]
    )
    executor = ScriptedChatModel(
        [
            AIMessage(content="尝试1"),
            AIMessage(content="尝试2"),
            AIMessage(content="尝试3"),
            AIMessage(content="新计划步骤完成"),
        ]
    )
    reflector = ScriptedChatModel(
        [
            ReflectionVerdict(verdict="retry", feedback="f1"),
            ReflectionVerdict(verdict="retry", feedback="f2"),
            ReflectionVerdict(verdict="retry", feedback="f3"),
            ReflectionVerdict(verdict="pass", reason="ok"),
        ]
    )
    ctx = _ctx(
        planner, executor, reflector, ScriptedChatModel([AIMessage(content="最终答案")]), bus
    )
    final, types = await _run_graph(ctx, "任务")
    assert final["final_answer"] == "最终答案"
    assert final["plan"][0]["id"] == "s2"  # 第二个计划生效
    assert final["plan"][0]["status"] == "done"
    assert len(planner.calls) == 2  # 重新规划确实发生
    assert "step_failed" in types
    assert len(final["reflection_log"]) == 4


async def test_abort_goes_to_finalizer_with_partial() -> None:
    bus = EventBus(uuid.uuid4(), persist=False)
    ctx = _ctx(
        ScriptedChatModel([Plan(steps=[PlanStep(id="s1", goal="不可完成")], rationale="")]),
        ScriptedChatModel([AIMessage(content="无法完成")]),
        ScriptedChatModel([ReflectionVerdict(verdict="abort", reason="任务不可行")]),
        ScriptedChatModel([AIMessage(content="无法完成任务的说明")]),
        bus,
    )
    final, types = await _run_graph(ctx, "任务")
    assert final["final_answer"] == "无法完成任务的说明"
    assert final["plan"][0]["status"] == "failed"
    assert "step_failed" in types


async def test_multi_step_advances_with_events() -> None:
    bus = EventBus(uuid.uuid4(), persist=False)
    ctx = _ctx(
        ScriptedChatModel(
            [
                Plan(
                    steps=[
                        PlanStep(id="s1", goal="第一步"),
                        PlanStep(id="s2", goal="第二步"),
                    ],
                    rationale="",
                )
            ]
        ),
        ScriptedChatModel([AIMessage(content="步骤1完成"), AIMessage(content="步骤2完成")]),
        ScriptedChatModel([ReflectionVerdict(verdict="pass"), ReflectionVerdict(verdict="pass")]),
        ScriptedChatModel([AIMessage(content="最终答案")]),
        bus,
    )
    final, types = await _run_graph(ctx, "任务")
    assert [s["status"] for s in final["plan"]] == ["done", "done"]
    assert types.count("step_start") == 2
    assert types.count("step_done") == 2
    assert len(final["reflection_log"]) == 2

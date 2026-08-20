"""M5 图级 HITL 单元测试（无 DB）：ask 策略 → interrupt 挂起 → 决议 → 继续。

- deny：工具不执行，is_error ToolMessage 回喂，agent 继续完成
- approve（session_wide）：工具执行 + 会话授权写入 state（checkpoint 持久化）
"""

import uuid

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent.graph import GraphContext, build_graph
from app.agent.schemas import Plan, PlanStep, ReflectionVerdict
from app.events.bus import EventBus
from app.llm.scripted import ScriptedChatModel
from app.tools.registry import build_registry


def _tool_call_msg() -> AIMessage:
    return AIMessage(
        content="我来调用计算器",
        tool_calls=[
            {"name": "calculator", "args": {"expression": "1+2"}, "id": "c1", "type": "tool_call"}
        ],
    )


def _ctx(executor_responses, *, session_wide: bool = False) -> GraphContext:
    return GraphContext(
        llms={
            "planner": ScriptedChatModel(
                [Plan(steps=[PlanStep(id="s1", goal="计算")], rationale="")]
            ),
            "executor": ScriptedChatModel(executor_responses),
            "reflector": ScriptedChatModel([ReflectionVerdict(verdict="pass", reason="ok")]),
            "finalizer": ScriptedChatModel([AIMessage(content="最终答案")]),
        },
        registry=build_registry(uuid.uuid4()),
        system_prompt="测试助手",
        bus=EventBus(uuid.uuid4(), persist=False),
        run_id=uuid.uuid4(),
        thread_id=uuid.uuid4(),
        tool_policy={"rules": [{"tool": "calculator", "action": "ask"}], "default": "allow"},
        session=None,
    )


async def _stream(graph, ctx: GraphContext, input_) -> dict | None:
    """流式执行；LangGraph 1.x 的 interrupt 以 __interrupt__ 更新项返回，此处提取其 payload。"""
    config = {"configurable": {"thread_id": str(ctx.thread_id)}}
    interrupted = None
    async for mode, payload in graph.astream(input_, config=config, stream_mode=["updates"]):
        if mode != "updates":
            continue
        if "__interrupt__" in payload:
            raw = payload["__interrupt__"]
            inter = raw[0] if isinstance(raw, (tuple, list)) else raw
            interrupted = inter.value if hasattr(inter, "value") else inter
    return interrupted


async def test_interrupt_deny_flow() -> None:
    ctx = _ctx([_tool_call_msg(), AIMessage(content="用户拒绝后我换方案完成")])
    graph = build_graph(ctx, checkpointer=InMemorySaver())  # interrupt 依赖 checkpoint

    interrupted = await _stream(graph, ctx, {"messages": [HumanMessage(content="任务")]})
    assert interrupted is not None, "应有 interrupt 挂起"
    pending = interrupted["pending"]
    assert [p["name"] for p in pending] == ["calculator"]
    assert pending[0]["args"] == {"expression": "1+2"}

    resumed = await _stream(
        graph, ctx, Command(resume={"action": "deny", "feedback": "不要用计算器"})
    )
    assert resumed is None
    snap = await graph.aget_state({"configurable": {"thread_id": str(ctx.thread_id)}})
    values = snap.values
    assert values["final_answer"] == "最终答案"
    # 工具未执行，回喂了拒绝消息
    tool_msgs = [m for m in values["messages"] if isinstance(m, ToolMessage)]
    assert any("拒绝" in m.content for m in tool_msgs)
    # 会话授权未写入
    assert values.get("tool_approvals") == {}


async def test_interrupt_approve_session_wide_flow() -> None:
    ctx = _ctx([_tool_call_msg(), AIMessage(content="计算完成")])
    graph = build_graph(ctx, checkpointer=InMemorySaver())

    interrupted = await _stream(graph, ctx, {"messages": [HumanMessage(content="任务")]})
    assert interrupted is not None
    await _stream(graph, ctx, Command(resume={"action": "approve", "session_wide": True}))
    snap = await graph.aget_state({"configurable": {"thread_id": str(ctx.thread_id)}})
    values = snap.values
    assert values["final_answer"] == "最终答案"
    # 会话级授权写入 state（随 checkpoint 持久化，后续 run 不再询问）
    assert values.get("tool_approvals") == {"calculator": "allow"}
    # 工具真实执行（结果回喂为 success 状态）
    tool_msgs = [m for m in values["messages"] if isinstance(m, ToolMessage)]
    assert any(m.status == "success" for m in tool_msgs)


async def test_deny_hidden_at_bind_time() -> None:
    """deny 策略：工具不出现在 LLM 工具列表（隐藏优于拦截）。"""
    ctx = _ctx([AIMessage(content="直接完成")])
    ctx.tool_policy = {"rules": [{"tool": "calculator", "action": "deny"}], "default": "allow"}
    graph = build_graph(ctx)
    await _stream(graph, ctx, {"messages": [HumanMessage(content="任务")]})
    executor = ctx.llms["executor"]
    assert executor.calls, "executor 应被调用"
    assert "calculator" not in executor.calls[0].tool_names

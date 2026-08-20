"""Agent 图（M1：executor ⇄ tools 循环；M4 扩展为 planner→executor⇄tools→reflector→finalizer）。

每次 run 编译一次图（LangGraph 编译开销毫秒级），通过 GraphContext 闭包注入
运行上下文（事件总线 / DB 会话 / 工具注册表），便于测试与 per-run 隔离。
"""

import asyncio
import uuid
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, SystemMessage, message_chunk_to_message
from langgraph.graph import END, START, StateGraph

from app.agent.state import AgentState
from app.core.errors import AppError
from app.events.bus import EventBus
from app.tools.executor import execute_tool_call
from app.tools.registry import ToolRegistry

_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _usage_dict(metadata) -> dict[str, int]:
    if not metadata:
        return {}
    return {k: int(metadata.get(k, 0)) for k in _USAGE_KEYS}


@dataclass
class GraphContext:
    llm_executor: object  # BaseChatModel（Anthropic 或 Scripted）
    registry: ToolRegistry
    system_prompt: str
    bus: EventBus
    run_id: uuid.UUID
    thread_id: uuid.UUID
    budgets: dict = field(default_factory=dict)
    session: object = None  # 运行任务的 DB 会话（工具落库用）


def build_graph(ctx: GraphContext):
    g = StateGraph(AgentState)
    max_llm_calls = int(ctx.budgets.get("max_llm_calls", 40))
    max_tool_calls = int(ctx.budgets.get("max_total_tool_calls", 30))

    async def executor_node(state: AgentState) -> dict:
        """执行一步：流式 LLM 输出 + 工具绑定；无工具调用即视为完成。"""
        if state.get("iteration_count", 0) >= max_llm_calls:
            raise AppError("budget_exceeded", "LLM 调用次数超限，已中止", retryable=False)
        tools = ctx.registry.langchain_tools()
        model = ctx.llm_executor.bind_tools(tools) if tools else ctx.llm_executor
        messages = [SystemMessage(content=ctx.system_prompt), *state.get("messages", [])]
        await ctx.bus.publish(ctx.bus.next_event("llm_start", {"node": "executor"}))

        merged = None
        async for chunk in model.astream(messages):
            merged = chunk if merged is None else merged + chunk
            delta = chunk.content
            if isinstance(delta, str) and delta:
                await ctx.bus.publish(
                    ctx.bus.next_event("llm_delta", {"node": "executor", "delta": delta})
                )

        message = message_chunk_to_message(merged) if merged is not None else AIMessage(content="")
        usage = _usage_dict(getattr(message, "usage_metadata", None))
        await ctx.bus.publish(ctx.bus.next_event("llm_end", {"node": "executor", "usage": usage}))

        prev_usage = state.get("usage_total") or {}
        result: dict = {
            "messages": [message],
            "iteration_count": state.get("iteration_count", 0) + 1,
            "usage_total": {k: prev_usage.get(k, 0) + v for k, v in usage.items()},
        }
        if not message.tool_calls:
            text = message.content if isinstance(message.content, str) else ""
            result["final_answer"] = text
        return result

    async def tools_node(state: AgentState) -> dict:
        """自定义工具节点（非 ToolNode）：权限校验（M5）+ 事件 + 落库 + 规范化。"""
        last = state["messages"][-1]
        calls = list(getattr(last, "tool_calls", None) or [])
        if not calls:
            return {}
        if state.get("tool_call_count", 0) + len(calls) > max_tool_calls:
            raise AppError("budget_exceeded", "工具调用次数超限，已中止", retryable=False)
        results = await asyncio.gather(
            *[
                execute_tool_call(
                    ctx.registry, tc, bus=ctx.bus, run_id=ctx.run_id, session=ctx.session
                )
                for tc in calls
            ]
        )
        return {
            "messages": list(results),
            "tool_call_count": state.get("tool_call_count", 0) + len(calls),
        }

    def route_after_executor(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    g.add_node("executor", executor_node)
    g.add_node("tools", tools_node)
    g.add_edge(START, "executor")
    g.add_conditional_edges("executor", route_after_executor, {"tools": "tools", END: END})
    g.add_edge("tools", "executor")
    return g.compile()

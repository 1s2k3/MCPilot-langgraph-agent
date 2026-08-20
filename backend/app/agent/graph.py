"""Agent 图（M3：load_context → executor ⇄ tools；M4 扩展 planner/reflector/finalizer）。

每次 run 编译一次图（LangGraph 编译开销毫秒级），通过 GraphContext 闭包注入
运行上下文（事件总线 / DB 会话 / 工具注册表），便于测试与 per-run 隔离。
"""

import asyncio
import uuid
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, message_chunk_to_message
from langgraph.graph import END, START, StateGraph

from app.agent.state import AgentState
from app.core.errors import AppError
from app.events.bus import EventBus
from app.memory.retriever import memory_context_text, retrieve_memories
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


def _latest_user_text(state: AgentState) -> str:
    for m in reversed(state.get("messages", [])):
        if m.type == "user" and isinstance(m.content, str):
            return m.content
    return ""


def _summarize_prompt(old_summary: str, messages) -> str:
    lines = []
    for m in messages:
        content = m.content if isinstance(m.content, str) else str(m.content)
        lines.append(f"{m.type}: {content[:500]}")
    return (
        "将以下对话与已有摘要合并为一段简短摘要（不超过 200 字），"
        "保留关键事实、用户偏好与任务结论：\n"
        f"已有摘要: {old_summary or '（无）'}\n"
        "新对话:\n" + "\n".join(lines)
    )


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
    window: int = 20  # 短期记忆消息窗口


def build_graph(ctx: GraphContext, checkpointer=None):
    g = StateGraph(AgentState)
    max_llm_calls = int(ctx.budgets.get("max_llm_calls", 40))
    max_tool_calls = int(ctx.budgets.get("max_total_tool_calls", 30))

    async def load_context_node(state: AgentState) -> dict:
        """读长期记忆：语义检索 top-k → memory_context（随 checkpoint 持久化）。"""
        query = _latest_user_text(state) or ""
        memories = await retrieve_memories(query) if query else []
        text = memory_context_text(memories)
        if memories:
            await ctx.bus.publish(
                ctx.bus.next_event("notice", {"memories_retrieved": len(memories)})
            )
        return {"memory_context": text}

    async def executor_node(state: AgentState) -> dict:
        """执行一步：流式 LLM 输出 + 工具绑定；无工具调用即视为完成。

        短期记忆策略（§5.6.1）：消息超窗口 → 先压缩溢出部分为滚动摘要，再截窗调用。
        """
        if state.get("iteration_count", 0) >= max_llm_calls:
            raise AppError("budget_exceeded", "LLM 调用次数超限，已中止", retryable=False)

        messages = state.get("messages", [])
        result: dict = {}
        # 超窗压缩（懒触发：仅超窗时多一次 LLM 调用）
        if len(messages) > ctx.window:
            overflow = messages[: len(messages) - ctx.window]
            prompt = _summarize_prompt(state.get("summary") or "", overflow)
            try:
                out = await ctx.llm_executor.ainvoke([HumanMessage(content=prompt)])
                result["summary"] = out.content if isinstance(out.content, str) else ""
            except Exception:  # noqa: BLE001
                pass  # 压缩失败不阻断，直接截窗

        tools = ctx.registry.langchain_tools()
        model = ctx.llm_executor.bind_tools(tools) if tools else ctx.llm_executor
        llm_input: list = [SystemMessage(content=ctx.system_prompt)]
        summary = state.get("summary") or ""
        if summary:
            llm_input.append(HumanMessage(content=f"[对话历史摘要] {summary}"))
        memory_ctx = state.get("memory_context") or ""
        if memory_ctx:
            llm_input.append(SystemMessage(content=memory_ctx))
        llm_input.extend(messages[-ctx.window :])

        await ctx.bus.publish(ctx.bus.next_event("llm_start", {"node": "executor"}))
        merged = None
        async for chunk in model.astream(llm_input):
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
        result.update(
            {
                "messages": [message],
                "iteration_count": state.get("iteration_count", 0) + 1,
                "usage_total": {k: prev_usage.get(k, 0) + v for k, v in usage.items()},
            }
        )
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

    g.add_node("load_context", load_context_node)
    g.add_node("executor", executor_node)
    g.add_node("tools", tools_node)
    g.add_edge(START, "load_context")
    g.add_edge("load_context", "executor")
    g.add_conditional_edges("executor", route_after_executor, {"tools": "tools", END: END})
    g.add_edge("tools", "executor")
    return g.compile(checkpointer=checkpointer)

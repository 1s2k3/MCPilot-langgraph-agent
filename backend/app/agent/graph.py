"""Agent 图（§5.3 完整拓扑）：

    START → load_context → planner → executor ⇄ tools
                                    executor（无工具调用，步骤完成）→ reflector
    reflector: pass(还有步骤) → executor | pass(最后一步) → finalizer
               retry → executor（注入反馈） | 重试耗尽 → planner
               replan → planner | abort → finalizer（部分结果）

控制流约定：
- reflector 在 state.next_node 中写入显式路由信号（可测试、可回放）
- 护栏：max_llm_calls / max_total_tool_calls / max_plan_steps / max_attempts_per_step
"""

import json
import uuid
from dataclasses import dataclass, field

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_chunk_to_message,
)
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.schemas import Plan, ReflectionVerdict
from app.agent.state import AgentState
from app.core.errors import AppError
from app.events.bus import EventBus
from app.memory.retriever import memory_context_text, retrieve_memories
from app.tools.executor import execute_tool_call, mask_secrets, to_json_safe
from app.tools.policy import resolve_tool_action
from app.tools.registry import ToolRegistry

_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

_DEFAULT_PLAN_PROMPT = """你是任务规划器。把用户任务拆解为按顺序执行的步骤（简单任务只需一个步骤）。
每个步骤目标要具体、可验证；在 tools_hint 中给出建议工具名。计划要最小化步骤数。"""

_REFLECT_PROMPT = """你是步骤评审器。对照步骤目标评审执行结果，输出评审结论：
- pass: 结果满足目标，可以进入下一步
- retry: 结果不满足，但修正后可以达成（feedback 给出具体修正意见）
- replan: 当前计划不可行，需要重新规划
- abort: 任务无法完成，应终止并如实说明
只依据证据评审，不臆测。"""

_FINALIZER_PROMPT = """你是最终回答生成器。基于已完成步骤的结果，生成面向用户的最终回答。
要求：直接回答用户任务；未完成的步骤要如实说明；不要调用任何工具。"""


def _usage_dict(metadata) -> dict[str, int]:
    if not metadata:
        return {}
    return {k: int(metadata.get(k, 0)) for k in _USAGE_KEYS}


def _latest_user_text(state: AgentState) -> str:
    for m in reversed(state.get("messages", [])):
        if isinstance(m, tuple):  # 输入阶段的 ("user", text) 形式
            if m[0] == "user":
                return str(m[1])
        # langchain 消息类型：HumanMessage.type == "human"（不是 "user"）
        elif m.type == "human" and isinstance(m.content, str):
            return m.content
    return ""


def _brief(m) -> dict:
    if isinstance(m, tuple):
        return {"role": m[0], "content": str(m[1])[:2000]}
    content = m.content
    brief: dict = {"role": m.type}
    if isinstance(content, str):
        brief["content"] = content[:2000]
    else:
        brief["content"] = str(content)[:500]
    if getattr(m, "tool_calls", None):
        # 评审证据视图同样脱敏（安全评审发现 3）
        brief["tool_calls"] = [
            {"name": tc.get("name"), "args": mask_secrets(tc.get("args"))} for tc in m.tool_calls
        ]
    return brief


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
    llms: dict  # {planner, executor, reflector, finalizer}
    registry: ToolRegistry
    system_prompt: str
    planner_prompt: str = ""
    bus: EventBus = None
    run_id: uuid.UUID | None = None
    thread_id: uuid.UUID | None = None
    budgets: dict = field(default_factory=dict)
    session: object = None  # 运行任务的 DB 会话（工具落库用）
    window: int = 20  # 短期记忆消息窗口
    tool_policy: dict = field(default_factory=dict)  # §5.9 权限策略


def build_graph(ctx: GraphContext, checkpointer=None):
    g = StateGraph(AgentState)
    max_llm_calls = int(ctx.budgets.get("max_llm_calls", 40))
    max_tool_calls = int(ctx.budgets.get("max_total_tool_calls", 30))
    max_plan_steps = int(ctx.budgets.get("max_plan_steps", 8))
    max_attempts = int(ctx.budgets.get("max_attempts_per_step", 3))

    async def _publish(type_: str, payload: dict | None = None) -> None:
        if ctx.bus is not None:
            await ctx.bus.publish(ctx.bus.next_event(type_, payload))

    async def _stream_llm(node: str, model, llm_input: list) -> AIMessage:
        """统一流式调用：llm_start/llm_delta/llm_end 事件 + usage。"""
        await _publish("llm_start", {"node": node})
        merged = None
        async for chunk in model.astream(llm_input):
            merged = chunk if merged is None else merged + chunk
            delta = chunk.content
            if isinstance(delta, str) and delta:
                await _publish("llm_delta", {"node": node, "delta": delta})
        message = message_chunk_to_message(merged) if merged is not None else AIMessage(content="")
        await _publish(
            "llm_end",
            {"node": node, "usage": _usage_dict(getattr(message, "usage_metadata", None))},
        )
        return message

    def _bump(state: AgentState, usage: dict | None = None) -> dict:
        out: dict = {"iteration_count": state.get("iteration_count", 0) + 1}
        if usage:
            prev = state.get("usage_total") or {}
            out["usage_total"] = {k: prev.get(k, 0) + v for k, v in usage.items()}
        return out

    async def load_context_node(state: AgentState) -> dict:
        """长期记忆检索注入（best-effort：数据库不可用时静默降级为空）。"""
        query = _latest_user_text(state) or ""
        try:
            memories = await retrieve_memories(query) if query else []
            if memories:
                await _publish("notice", {"memories_retrieved": len(memories)})
            return {"memory_context": memory_context_text(memories)}
        except Exception:  # noqa: BLE001
            return {"memory_context": ""}

    async def planner_node(state: AgentState) -> dict:
        """产出步骤计划（结构化输出）。带记忆上下文与失败步骤/反思历史。"""
        if state.get("iteration_count", 0) >= max_llm_calls:
            raise AppError("budget_exceeded", "LLM 调用次数超限，已中止", retryable=False)
        msgs: list = [SystemMessage(content=ctx.planner_prompt or _DEFAULT_PLAN_PROMPT)]
        memory_ctx = state.get("memory_context") or ""
        if memory_ctx:
            msgs.append(SystemMessage(content=memory_ctx))
        context: dict = {"任务": _latest_user_text(state)}
        failed = [s for s in (state.get("plan") or []) if s.get("status") == "failed"]
        if failed:
            context["已失败步骤"] = failed
        reflection = state.get("reflection_log") or []
        if reflection:
            context["近期反思"] = reflection[-3:]
        msgs.append(HumanMessage(content=json.dumps(context, ensure_ascii=False, default=str)))

        out = await ctx.llms["planner"].with_structured_output(Plan).ainvoke(msgs)
        steps = [
            s.model_dump() | {"status": "pending", "attempts": 0, "feedback": []}
            for s in out.steps[:max_plan_steps]
        ]
        if not steps:
            raise AppError("planning_failed", "planner 未产出任何步骤", retryable=False)
        steps[0]["status"] = "in_progress"
        await _publish(
            "plan_created",
            {
                "steps": steps,
                "rationale": out.rationale,
                "truncated": len(out.steps) > max_plan_steps,
            },
        )
        await _publish("step_start", {"index": 0, "goal": steps[0]["goal"]})
        result = _bump(state)
        result.update({"plan": steps, "current_step_index": 0})
        return result

    async def executor_node(state: AgentState) -> dict:
        """执行当前步骤：可调工具；一轮无工具调用即视为步骤完成（交 reflector 评审）。"""
        if state.get("iteration_count", 0) >= max_llm_calls:
            raise AppError("budget_exceeded", "LLM 调用次数超限，已中止", retryable=False)
        plan = state.get("plan") or []
        idx = state.get("current_step_index", 0)
        if idx >= len(plan):
            return {"next_node": "finalizer"}
        step = plan[idx]

        messages = state.get("messages", [])
        result: dict = {}
        # 超窗压缩（懒触发）
        if len(messages) > ctx.window:
            overflow = messages[: len(messages) - ctx.window]
            prompt = _summarize_prompt(state.get("summary") or "", overflow)
            try:
                out = await ctx.llms["executor"].ainvoke([HumanMessage(content=prompt)])
                result["summary"] = out.content if isinstance(out.content, str) else ""
            except Exception:  # noqa: BLE001
                pass

        # deny 工具在绑定阶段即隐藏（§5.9：隐藏优于执行时拦截）
        tools = [
            m.tool
            for m in ctx.registry.all_metas()
            if resolve_tool_action(ctx.tool_policy, m.tool.name, m.server) != "deny"
        ]
        model = ctx.llms["executor"].bind_tools(tools) if tools else ctx.llms["executor"]
        llm_input: list = [SystemMessage(content=ctx.system_prompt)]
        llm_input.append(
            SystemMessage(
                content=(
                    f"当前步骤目标（第 {idx + 1}/{len(plan)} 步）: {step['goal']}\n"
                    "完成本步骤后给出结果说明；不要生成面向用户的最终回答。"
                )
            )
        )
        feedback = (step.get("feedback") or [])[-1:] or []
        if feedback:
            llm_input.append(
                SystemMessage(content=f"反思反馈（上次尝试的问题，请修正）: {feedback[-1]}")
            )
        summary = state.get("summary") or ""
        if summary:
            llm_input.append(HumanMessage(content=f"[对话历史摘要] {summary}"))
        memory_ctx = state.get("memory_context") or ""
        if memory_ctx:
            llm_input.append(SystemMessage(content=memory_ctx))
        llm_input.extend(messages[-ctx.window :])

        message = await _stream_llm("executor", model, llm_input)
        result.update({"messages": [message]})
        result.update(_bump(state, _usage_dict(getattr(message, "usage_metadata", None))))
        return result

    async def tools_node(state: AgentState) -> dict:
        """工具节点：权限校验（ask → interrupt HITL）→ 顺序执行 → 事件 + 落库 + 规范化。

        混合场景（部分批准部分拒绝）单遍处理：结果按 tool_calls 顺序回喂 LLM。
        """
        last = state["messages"][-1]
        calls = list(getattr(last, "tool_calls", None) or [])
        if not calls:
            return {}
        if state.get("tool_call_count", 0) + len(calls) > max_tool_calls:
            raise AppError("budget_exceeded", "工具调用次数超限，已中止", retryable=False)

        approvals = dict(state.get("tool_approvals") or {})
        pending: list[dict] = []
        for tc in calls:
            meta = ctx.registry.get(tc.get("name"))
            server = meta.server if meta is not None else "local"
            if (
                resolve_tool_action(ctx.tool_policy, tc.get("name"), server) == "ask"
                and approvals.get(tc.get("name")) != "allow"
            ):
                pending.append(
                    {
                        "name": tc.get("name"),
                        "args": to_json_safe(mask_secrets(tc.get("args") or {})),
                        "id": tc.get("id"),
                    }
                )
        denied_ids: set[str] = set()
        denied_feedback = ""
        if pending:
            if checkpointer is None:
                # interrupt 依赖 checkpoint 持久化；不可用时明确失败而非静默拒绝
                raise AppError(
                    "checkpoint_required",
                    "工具审批（ask 策略）需要 checkpoint 持久化，当前数据库不可用",
                    retryable=False,
                )
            # HITL：挂起等待人工决议（resume API → Command(resume=...)）
            decision = interrupt({"pending": pending}) or {}
            if decision.get("action") == "approve":
                if decision.get("session_wide"):
                    for item in pending:
                        approvals[item["name"]] = "allow"
            else:
                denied_ids = {item["id"] for item in pending}
                denied_feedback = decision.get("feedback") or ""

        results_by_id: dict[str, ToolMessage] = {}
        for tc in calls:
            if tc.get("id") in denied_ids:
                # deny：is_error ToolMessage 回喂，让 agent 自行改道（§5.9）
                msg = "用户拒绝了该工具调用。"
                if denied_feedback:
                    msg += f"用户反馈: {denied_feedback}"
                results_by_id[tc.get("id")] = ToolMessage(
                    content=msg, tool_call_id=tc.get("id"), status="error"
                )
                continue
            meta = ctx.registry.get(tc.get("name"))
            if meta is None:
                results_by_id[tc.get("id")] = ToolMessage(
                    content=f"未知工具: {tc.get('name')}",
                    tool_call_id=tc.get("id"),
                    status="error",
                )
                continue
            results_by_id[tc.get("id")] = await execute_tool_call(
                meta, tc, bus=ctx.bus, run_id=ctx.run_id, session=ctx.session
            )
        ordered = [results_by_id[tc.get("id")] for tc in calls if tc.get("id") in results_by_id]
        return {
            "messages": ordered,
            "tool_call_count": state.get("tool_call_count", 0) + len(calls),
            "tool_approvals": approvals,
        }

    async def reflector_node(state: AgentState) -> dict:
        """评审步骤结果：pass / retry（注入反馈）/ replan / abort。"""
        if state.get("iteration_count", 0) >= max_llm_calls:
            raise AppError("budget_exceeded", "LLM 调用次数超限，已中止", retryable=False)
        plan = state.get("plan") or []
        idx = state.get("current_step_index", 0)
        step = plan[idx]
        last = state["messages"][-1] if state.get("messages") else None
        evidence = json.dumps(
            {
                "步骤目标": step["goal"],
                "步骤结果": last.content
                if last is not None and isinstance(last.content, str)
                else "",
                "最近交互": [_brief(m) for m in state.get("messages", [])[-6:]],
                "已尝试次数": step.get("attempts", 0),
            },
            ensure_ascii=False,
            default=str,
        )
        verdict = (
            await ctx.llms["reflector"]
            .with_structured_output(ReflectionVerdict)
            .ainvoke([SystemMessage(content=_REFLECT_PROMPT), HumanMessage(content=evidence)])
        )
        entry = {
            "step_id": step["id"],
            "step_index": idx,
            "verdict": verdict.verdict,
            "reason": verdict.reason,
            "feedback": verdict.feedback,
        }
        reflection_log = list(state.get("reflection_log") or []) + [entry]
        await _publish("reflect", entry)
        result = _bump(state)
        result["reflection_log"] = reflection_log

        if verdict.verdict == "pass":
            step["status"] = "done"
            await _publish("step_done", {"index": idx, "goal": step["goal"]})
            if idx + 1 >= len(plan):
                result.update({"plan": plan, "next_node": "finalizer"})
            else:
                plan[idx + 1]["status"] = "in_progress"
                await _publish("step_start", {"index": idx + 1, "goal": plan[idx + 1]["goal"]})
                result.update(
                    {"plan": plan, "current_step_index": idx + 1, "next_node": "executor"}
                )
            return result

        step["attempts"] = step.get("attempts", 0) + 1
        if verdict.verdict == "retry" and step["attempts"] < max_attempts:
            step["feedback"].append(verdict.feedback)
            result.update({"plan": plan, "next_node": "executor"})
            return result

        # retry 耗尽 / replan / abort → 步骤失败
        step["status"] = "failed"
        await _publish(
            "step_failed",
            {
                "index": idx,
                "goal": step["goal"],
                "verdict": verdict.verdict,
                "reason": verdict.reason,
            },
        )
        next_node = "planner" if verdict.verdict != "abort" else "finalizer"
        result.update({"plan": plan, "next_node": next_node})
        return result

    async def finalizer_node(state: AgentState) -> dict:
        """汇总已完成的步骤，生成最终回答（不使用工具）。"""
        if state.get("iteration_count", 0) >= max_llm_calls:
            raise AppError("budget_exceeded", "LLM 调用次数超限，已中止", retryable=False)
        plan = state.get("plan") or []
        context = json.dumps(
            {
                "用户任务": _latest_user_text(state),
                "已完成步骤": [s for s in plan if s.get("status") == "done"],
                "未完成步骤": [s for s in plan if s.get("status") != "done"],
                "反思记录": state.get("reflection_log") or [],
            },
            ensure_ascii=False,
            default=str,
        )
        message = await _stream_llm(
            "finalizer",
            ctx.llms["finalizer"],
            [SystemMessage(content=_FINALIZER_PROMPT), HumanMessage(content=context)],
        )
        text = message.content if isinstance(message.content, str) else ""
        result = _bump(state, _usage_dict(getattr(message, "usage_metadata", None)))
        result.update({"messages": [message], "final_answer": text})
        return result

    def route_after_executor(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else "reflector"

    def route_after_reflector(state: AgentState) -> str:
        return state.get("next_node") or "finalizer"

    g.add_node("load_context", load_context_node)
    g.add_node("planner", planner_node)
    g.add_node("executor", executor_node)
    g.add_node("tools", tools_node)
    g.add_node("reflector", reflector_node)
    g.add_node("finalizer", finalizer_node)
    g.add_edge(START, "load_context")
    g.add_edge("load_context", "planner")
    g.add_edge("planner", "executor")
    g.add_conditional_edges(
        "executor", route_after_executor, {"tools": "tools", "reflector": "reflector"}
    )
    g.add_edge("tools", "executor")
    g.add_conditional_edges(
        "reflector",
        route_after_reflector,
        {"executor": "executor", "planner": "planner", "finalizer": "finalizer"},
    )
    g.add_edge("finalizer", END)
    return g.compile(checkpointer=checkpointer)

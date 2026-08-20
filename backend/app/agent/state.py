"""Agent 状态（LangGraph StateGraph schema）。

随里程碑扩展：
- M4: plan / current_step_index / reflection_log
- M5: tool_approvals（会话级工具授权，随 checkpoint 持久化）
注意：所有值必须 JSON 可序列化（checkpoint 持久化要求）。
"""

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], operator.add]
    iteration_count: int  # LLM 调用次数（护栏）
    tool_call_count: int  # 工具调用次数（护栏）
    usage_total: dict[str, int]  # token 用量汇总
    final_answer: str | None

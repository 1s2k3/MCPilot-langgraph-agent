"""Agent 状态（LangGraph StateGraph schema）。

M5 待扩展：tool_approvals（会话级工具授权，随 checkpoint 持久化）。
注意：所有值必须 JSON 可序列化（checkpoint 持久化要求）。
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]  # 标准 reducer：dict→BaseMessage 转换（Studio/Server 输入）
    iteration_count: int  # LLM 调用次数（护栏）
    tool_call_count: int  # 工具调用次数（护栏）
    usage_total: dict[str, int]  # token 用量汇总
    final_answer: str | None
    summary: str  # 短期记忆滚动摘要（窗口超限时压缩生成）
    memory_context: str  # 长期记忆检索注入（load_context 节点产出）
    plan: list[dict] | None  # 步骤计划（§5.4 契约的 JSON 化）
    current_step_index: int  # 当前步骤
    reflection_log: list[dict]  # 反思记录（UI 可视化 + 评估指标数据源）
    next_node: str  # reflector 路由信号（显式控制流）
    tool_approvals: dict[str, str]  # 会话级工具授权（{"tool": "allow"}，随 checkpoint 持久化）

"""事件契约（与 API 文档 §7.2 一致，events 表与 SSE 共用 schema）。

事件类型（随里程碑扩展）：
  M1: run_start | llm_start | llm_delta | llm_end | tool_call_start | tool_call_end
      | notice | error | run_end
  M3: state_snapshot | memory_write
  M4: plan_created | step_start | step_done | step_failed | reflect
  M5: interrupt | resumed
"""

from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    seq: int
    ts: str  # ISO8601 UTC
    run_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


# 终态事件：SSE 收到后关闭流
TERMINAL_EVENT_TYPES = frozenset({"run_end"})

"""Planner / Reflector 的结构化输出契约（output_config.format 约束）。"""

from typing import Literal

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    id: str = Field(min_length=1, max_length=50)
    goal: str = Field(min_length=1, max_length=500)
    tools_hint: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    steps: list[PlanStep]
    rationale: str = ""


class ReflectionVerdict(BaseModel):
    verdict: Literal["pass", "retry", "replan", "abort"]
    feedback: str = ""  # retry 时给 executor 的修正意见
    reason: str = ""  # 评审依据


# 步骤状态机（状态在 state.plan[i].status 上流转，随 checkpoint 持久化）
# pending → in_progress → done / failed（skipped 预留）

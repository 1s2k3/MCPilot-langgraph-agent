"""API 请求/响应模型。"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---- Agents ----
class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    system_prompt: str = ""
    planner_prompt: str = ""
    node_models: dict[str, dict] = Field(default_factory=dict)
    budgets: dict[str, int] = Field(default_factory=dict)
    tool_policy: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    planner_prompt: str | None = None
    node_models: dict[str, dict] | None = None
    budgets: dict[str, int] | None = None
    tool_policy: dict[str, Any] | None = None
    enabled: bool | None = None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    system_prompt: str
    planner_prompt: str
    node_models: dict
    budgets: dict
    tool_policy: dict
    enabled: bool
    created_at: datetime
    updated_at: datetime


# ---- Threads ----
class ThreadCreate(BaseModel):
    agent_id: uuid.UUID | None = None
    title: str = ""


class ThreadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID | None
    title: str
    status: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    tool_calls: dict | None
    seq: int
    run_id: uuid.UUID | None
    created_at: datetime


# ---- Runs ----
class RunCreate(BaseModel):
    input: str = Field(min_length=1)
    agent_id: uuid.UUID | None = None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    thread_id: uuid.UUID
    agent_id: uuid.UUID | None
    status: str
    input: str
    final_answer: str | None
    usage: dict
    latency_ms: int | None
    error: dict | None
    created_at: datetime
    finished_at: datetime | None

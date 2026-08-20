"""数据模型 —— 与 alembic/versions/0001_initial.py 一一对应。

约定：
- 主键 UUID（应用侧生成）
- JSONB 承载可变结构（policy / budgets / payload）
- 时间戳统一 TIMESTAMPTZ
- 业务表不保存明文密钥（见 api_keys.key_ciphertext、mcp_servers.headers_encrypted）
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Agent(Base):
    """Agent 配置：提示词、节点模型、预算护栏、工具权限策略。"""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    planner_prompt: Mapped[str] = mapped_column(Text, default="")
    node_models: Mapped[dict] = mapped_column(JSONB, default=dict)
    budgets: Mapped[dict] = mapped_column(JSONB, default=dict)
    tool_policy: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Thread(Base):
    """会话（LangGraph thread，thread_id ↔ id）。"""

    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | archived
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Run(Base):
    """一次"用户输入 → 最终回答"的运行。"""

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True
    )  # pending|running|interrupted|completed|failed|cancelled
    input: Mapped[str] = mapped_column(Text)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict] = mapped_column(JSONB, default=dict)  # token 用量汇总
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Message(Base):
    """会话消息（前端渲染 + 评估数据源）。"""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user|assistant|tool|system|summary
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    token_usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    seq: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("thread_id", "seq", name="uq_messages_thread_seq"),)


class ToolCall(Base):
    """工具执行记录（结果管理：截断/脱敏/状态）。"""

    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(200))
    server: Mapped[str] = mapped_column(String(100))  # local | <mcp server 名>
    args: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending|running|succeeded|failed|denied
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    """运行事件流（Timeline 数据源 + SSE 断线补拉）。"""

    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_events_run_seq"),)


class Memory(Base):
    """长期记忆（pgvector 语义检索）。"""

    __tablename__ = "memories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("threads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(20), default="fact")  # fact | preference
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class McpServer(Base):
    """MCP 服务器配置。凭据（headers）加密存储，见 app/security/keys.py。"""

    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    transport: Mapped[str] = mapped_column(String(30))  # stdio|streamable_http|sse|websocket
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)  # stdio 用
    args: Mapped[list | None] = mapped_column(JSONB, default=list)  # stdio 用
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # 远程用
    headers_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    env: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tool_allowlist: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # None=全部
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ApiKey(Base):
    """外部服务 API Key（密文存储，写后不可读）。"""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(50), index=True)  # anthropic|langsmith|...
    name: Mapped[str] = mapped_column(String(200))
    key_ciphertext: Mapped[str] = mapped_column(Text)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalDataset(Base):
    """评估数据集。

    entries 条目: {input, expected_tool_calls, reference_answer, rubric, category, difficulty}
    """

    __tablename__ = "eval_datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    entries: Mapped[list | None] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvalRun(Base):
    """一次评估运行。"""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_datasets.id", ondelete="CASCADE")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending|running|completed|failed
    model_snapshot: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    metrics: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvalScore(Base):
    """逐条评分（含完整轨迹，供钻取分析）。"""

    __tablename__ = "eval_scores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    eval_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"))
    entry_index: Mapped[int] = mapped_column(Integer)
    input: Mapped[str] = mapped_column(Text)
    trajectory: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    tool_seq_match: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    answer_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 1-5
    judge_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("eval_run_id", "entry_index", name="uq_eval_scores_entry"),)

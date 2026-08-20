"""应用配置：pydantic-settings，环境变量 / .env 驱动。

.env 查找顺序：仓库根目录（backend 的上级）优先，其次 backend/.env。
"""

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    # ---- 数据库 ----
    database_url: str = (
        "postgresql+psycopg://agent:agent@localhost:5432/agent_platform?connect_timeout=3"
    )

    # ---- 安全 ----
    app_master_key: SecretStr | None = None  # Fernet 主密钥；未配置时 API Key 功能禁用
    admin_token: SecretStr | None = None  # 可选网关级鉴权

    # ---- LLM ----
    anthropic_api_key: SecretStr | None = None
    default_model: str = "claude-opus-5"
    # 脚本化假 LLM（测试 / 离线演示 / LIVE_LLM=0 评估）；无 API Key 时自动生效
    scripted_llm: bool = False
    # 各节点可覆盖 {model, effort}；空 dict 表示用默认。effort 默认: executor=high, 其余=medium
    node_models: dict = {
        "planner": {},
        "executor": {},
        "reflector": {},
        "finalizer": {},
        "memory_extractor": {},
        "judge": {},
    }

    # ---- 预算护栏（per-agent 可覆盖，这里给全局默认） ----
    max_plan_steps: int = 8
    max_attempts_per_step: int = 3
    max_total_tool_calls: int = 30
    max_llm_calls: int = 40
    run_timeout_seconds: int = 600
    llm_call_timeout_seconds: int = 180

    # ---- Memory ----
    short_term_window: int = 20  # 短期记忆消息窗口
    memory_top_k: int = 8  # 长期记忆检索条数
    embedding_provider: str = "local"  # local | disabled
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"  # 384 维
    embedding_dim: int = 384

    # ---- Tool 结果管理 ----
    tool_result_cap_bytes: int = 102_400  # 结果落库上限（超出截断）
    tool_feedback_cap_chars: int = 20_000  # 回喂 LLM 的文本上限
    tool_timeout_seconds: int = 60  # 单次工具执行超时

    # ---- MCP ----
    seed_demo_mcp: bool = True  # 首次启动播种演示 server（mcp-demo）
    mcp_demo_url: str | None = None  # 设置后 demo server 走 streamable_http（compose 用）

    # ---- Observability ----
    langchain_tracing_v2: bool = False
    langchain_api_key: SecretStr | None = None
    langchain_project: str = "agent-platform"

    # ---- Web ----
    cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v: object) -> object:
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()

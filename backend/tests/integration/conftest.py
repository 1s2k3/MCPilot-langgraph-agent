"""集成测试：需要真实 PostgreSQL（docker compose / CI 服务）。

本地无数据库时自动跳过（pytest.skip），保证单元测试链路不受环境约束。
"""

import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

# 必须在任何 app 模块导入前设置：集成测试用 ScriptedLLM 确定性驱动 + 密钥加密可用
os.environ.setdefault("SCRIPTED_LLM", "true")
os.environ.setdefault("APP_MASTER_KEY", Fernet.generate_key().decode())


@pytest.fixture(scope="session")
async def db_available() -> None:
    """会话级守卫：数据库不可达 → 跳过整个集成测试会话。"""
    from app.db.session import engine

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用，跳过集成测试: {exc}")

"""FastAPI 公共依赖。"""

from collections.abc import AsyncIterator

from fastapi import Header
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import SessionLocal


async def get_session() -> AsyncIterator[AsyncSession]:
    """请求级数据库会话。"""
    async with SessionLocal() as session:
        yield session


async def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """可选网关鉴权：配置了 ADMIN_TOKEN 时，所有 API 需携带 X-Admin-Token。"""
    token = get_settings().admin_token
    # 空字符串视为未配置（pydantic 会把空 env 解析为空 SecretStr 而非 None）
    if token is None or not token.get_secret_value():
        return
    if x_admin_token is None or x_admin_token != token.get_secret_value():
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "unauthorized",
                    "message": "无效的管理令牌",
                    "retryable": False,
                    "details": {},
                }
            },
        )

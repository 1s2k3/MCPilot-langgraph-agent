from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    """健康检查：数据库连通性。"""
    db = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        db = "error"
    return {"status": "ok" if db == "ok" else "degraded", "db": db, "version": "0.1.0"}

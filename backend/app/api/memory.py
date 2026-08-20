"""长期记忆 API：列表 / 语义搜索 / 编辑 / 删除（前端 Memory 面板数据源）。"""

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import not_found
from app.db.models import Memory
from app.memory.embedder import aembed
from app.memory.retriever import retrieve_memories

router = APIRouter(tags=["memory"])


class MemoryUpdate(BaseModel):
    content: str | None = None
    type: str | None = Field(default=None, pattern="^(fact|preference)$")
    importance: float | None = Field(default=None, ge=0.0, le=1.0)


def _memory_out(row: Memory) -> dict:
    return {
        "id": str(row.id),
        "type": row.type,
        "content": row.content,
        "importance": row.importance,
        "thread_id": str(row.thread_id) if row.thread_id else None,
        "source_run_id": str(row.source_run_id) if row.source_run_id else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/memories")
async def list_memories(
    session: AsyncSession = Depends(get_session), limit: int = Query(default=100, le=500)
) -> dict:
    rows = (
        (await session.execute(select(Memory).order_by(Memory.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return {"memories": [_memory_out(r) for r in rows]}


@router.get("/memories/search")
async def search_memories(q: str, k: int = Query(default=8, ge=1, le=50)) -> dict:
    return {"memories": await retrieve_memories(q, k=k)}


@router.get("/threads/{thread_id}/memories")
async def thread_memories(
    thread_id: uuid.UUID,
    q: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    if q:
        return {"memories": await retrieve_memories(q, thread_id=thread_id)}
    rows = (
        (
            await session.execute(
                select(Memory)
                .where(Memory.thread_id == thread_id)
                .order_by(Memory.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {"memories": [_memory_out(r) for r in rows]}


@router.patch("/memories/{memory_id}")
async def update_memory(
    memory_id: uuid.UUID, body: MemoryUpdate, session: AsyncSession = Depends(get_session)
) -> dict:
    row = await session.get(Memory, memory_id)
    if row is None:
        raise not_found("Memory", str(memory_id))
    updates = body.model_dump(exclude_unset=True)
    content = updates.pop("content", None)
    for key, value in updates.items():
        setattr(row, key, value)
    if content is not None:
        row.content = content
        emb = await aembed([content])
        if emb:
            row.embedding = emb[0]
    await session.commit()
    return _memory_out(row)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    row = await session.get(Memory, memory_id)
    if row is None:
        raise not_found("Memory", str(memory_id))
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

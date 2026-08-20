"""长期记忆检索（§5.6.2）：pgvector 余弦相似度 top-k + 上下文拼装。"""

import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Memory
from app.db.session import SessionLocal
from app.memory.embedder import aembed


async def retrieve_memories(
    query: str, *, thread_id: uuid.UUID | None = None, k: int | None = None
) -> list[dict]:
    """语义检索 top-k；embedding 禁用或无结果时返回 []。"""
    s = get_settings()
    vec = await aembed([query])
    if vec is None:
        return []
    target = vec[0]
    async with SessionLocal() as session:
        stmt = (
            select(
                Memory.id,
                Memory.type,
                Memory.content,
                Memory.importance,
                Memory.source_run_id,
                Memory.thread_id,
                Memory.created_at,
                Memory.embedding.cosine_distance(target).label("distance"),
            )
            .where(Memory.embedding.is_not(None))
            .order_by(Memory.embedding.cosine_distance(target))
            .limit(k or s.memory_top_k)
        )
        if thread_id is not None:
            stmt = stmt.where(Memory.thread_id == thread_id)
        rows = (await session.execute(stmt)).all()
    return [
        {
            "id": str(r.id),
            "type": r.type,
            "content": r.content,
            "importance": r.importance,
            "source_run_id": str(r.source_run_id) if r.source_run_id else None,
            "thread_id": str(r.thread_id) if r.thread_id else None,
            "similarity": round(max(0.0, 1.0 - float(r.distance)), 4),
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


async def delete_top_match(
    query: str, *, thread_id: uuid.UUID | None, threshold: float = 0.7
) -> str | None:
    """语义删除：top-1 相似度 ≥ threshold 才删除，返回被删内容（未命中 None）。"""
    vec = await aembed([query])
    if vec is None:
        return None
    target = vec[0]
    async with SessionLocal() as session:
        hit = (
            await session.execute(
                select(Memory, Memory.embedding.cosine_distance(target).label("d"))
                .where(Memory.embedding.is_not(None))
                .order_by(Memory.embedding.cosine_distance(target))
                .limit(1)
            )
        ).one_or_none()
        if hit is None:
            return None
        row, distance = hit
        if 1.0 - float(distance) < threshold:
            return None
        content = row.content
        await session.delete(row)
        await session.commit()
        return content


def memory_context_text(memories: list[dict]) -> str:
    """检索结果 → 注入 planner/executor 的上下文文本。"""
    if not memories:
        return ""
    lines = [f"- [{m['type']}] {m['content']}（相似度 {m['similarity']}）" for m in memories]
    return "以下是长期记忆中与当前任务相关的信息（供参考，无需向用户复述）：\n" + "\n".join(lines)

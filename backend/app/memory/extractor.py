"""长期记忆提取与写入（§5.6.2）：LLM 结构化提取 → 规范化去重 → embedding 落库。

设计约定：提取是 best-effort —— LLM 失败/无 Key/无脚本时静默跳过，绝不影响主运行。
"""

import re
import uuid
from typing import Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger
from app.db.models import Memory
from app.db.session import SessionLocal
from app.memory.embedder import aembed

logger = get_logger(__name__)


class MemoryItem(BaseModel):
    type: Literal["fact", "preference"] = "fact"
    content: str = Field(min_length=1)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class MemoryExtraction(BaseModel):
    memories: list[MemoryItem] = []


_EXTRACTION_PROMPT = """从以下对话中提取值得跨会话记住的信息（用户事实、偏好、长期目标）。
只提取可复用的信息；忽略一次性任务细节、计算中间结果与寒暄。无值得记住的内容时返回空列表。

对话：
{conversation}
"""


def normalize_content(text: str) -> str:
    """规范化：压缩空白 + 小写（去重键，不用于展示）。"""
    return re.sub(r"\s+", " ", text.strip()).lower()


async def extract_memories(llm, conversation: str) -> list[MemoryItem]:
    """LLM 结构化提取；任何失败返回 []。"""
    try:
        chain = llm.with_structured_output(MemoryExtraction)
        out = await chain.ainvoke(
            [HumanMessage(content=_EXTRACTION_PROMPT.format(conversation=conversation))]
        )
        return out.memories
    except Exception:  # noqa: BLE001
        logger.warning("memory_extraction_failed")
        return []


async def store_single_memory(
    thread_id: uuid.UUID | None, source_run_id: uuid.UUID | None, item: MemoryItem
) -> int:
    """写入单条记忆（去重 upsert + embedding）。返回新增条数（0=已存在合并）。"""
    async with SessionLocal() as session:
        try:
            written = await _upsert(session, thread_id, source_run_id, item)
            await session.commit()
            return written
        except Exception:  # noqa: BLE001
            logger.warning("store_single_memory_failed", content=item.content[:50])
            await session.rollback()
            return 0


async def store_extracted_memories(
    thread_id: uuid.UUID | None, source_run_id: uuid.UUID | None, llm, conversation: str
) -> int:
    """提取 + 逐条独立落库（每条独立事务，FK 违规不影响其他条目）。返回新增条数。"""
    items = await extract_memories(llm, conversation)
    if not items:
        return 0
    written = 0
    for item in items:
        written += await store_single_memory(thread_id, source_run_id, item)
    return written


async def _upsert(session, thread_id, source_run_id, item: MemoryItem) -> int:
    """规范化去重：同 thread 内内容相同 → 合并（取高 importance）。"""
    key = normalize_content(item.content)
    existing = (
        (
            await session.execute(
                select(Memory).where(Memory.thread_id == thread_id, Memory.content == key)
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        existing.importance = max(existing.importance, item.importance)
        existing.type = item.type
        return 0
    try:
        emb = await aembed([item.content])
    except Exception:  # noqa: BLE001
        logger.warning("embed_failed_skipping_memory", content=item.content[:50])
        return 0
    try:
        session.add(
            Memory(
                thread_id=thread_id,
                source_run_id=source_run_id,
                type=item.type,
                content=item.content,
                importance=item.importance,
                embedding=emb[0] if emb else None,
            )
        )
        await session.flush()
    except IntegrityError:
        logger.warning(
            "memory_fk_violation_skipped",
            thread_id=str(thread_id),
            source_run_id=str(source_run_id),
        )
        await session.rollback()
        return 0
    return 1

"""长期记忆工具（Agent 主动读写）：remember_memory / forget_memory。

thread_id 经闭包绑定（每个 run 的注册表独立构建）。
"""

import uuid

from langchain_core.tools import tool

from app.memory.extractor import MemoryItem, store_single_memory
from app.memory.retriever import delete_top_match


def build_memory_tools(thread_id: uuid.UUID | None):
    @tool
    async def remember_memory(content: str, type: str = "fact", importance: float = 0.5) -> str:
        """把一条值得跨会话记住的信息写入长期记忆。type: fact（事实）或 preference（偏好）。"""
        written = await store_single_memory(
            thread_id,
            None,
            MemoryItem(type=type, content=content, importance=importance),
        )
        return "已记住。" if written else "已存在相同记忆，已合并。"

    @tool
    async def forget_memory(query: str) -> str:
        """按语义删除与 query 最相似的一条记忆（相似度不足则不做任何事）。"""
        removed = await delete_top_match(query, thread_id=thread_id, threshold=0.7)
        return f"已删除记忆: {removed}" if removed else "未找到足够相似的记忆，未删除。"

    return remember_memory, forget_memory

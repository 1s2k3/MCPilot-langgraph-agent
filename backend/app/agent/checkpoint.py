"""LangGraph Postgres Checkpointer 生命周期管理。

- 应用级单例，连接保持打开（AsyncPostgresSaver 上下文管理器退出即断连）
- 数据库不可达时返回 None（平台降级运行：无持久化但不崩溃）
- LANGGRAPH_STRICT_MSGPACK 必须在 langgraph 导入前设置（checkpoint 反序列化安全）
"""

import asyncio
import os

os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.logging import get_logger  # noqa: E402

logger = get_logger(__name__)

_checkpointer: AsyncPostgresSaver | None = None
_cm = None  # 保持引用以便关闭时 __aexit__
_lock = asyncio.Lock()


def _conn_string() -> str:
    """SQLAlchemy URL → psycopg 原生 URL（checkpointer 使用 psycopg3）。"""
    url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    if "connect_timeout" not in url:
        url += ("&" if "?" in url else "?") + "connect_timeout=3"
    return url


async def get_checkpointer() -> AsyncPostgresSaver | None:
    """获取（懒初始化）checkpointer；失败返回 None。"""
    global _checkpointer, _cm
    if _checkpointer is not None:
        return _checkpointer
    async with _lock:
        if _checkpointer is not None:
            return _checkpointer
        try:
            cm = AsyncPostgresSaver.from_conn_string(_conn_string())
            saver = await cm.__aenter__()
            await saver.setup()  # 建表/迁移（checkpoints / checkpoint_blobs / checkpoint_writes）
        except Exception:  # noqa: BLE001
            logger.warning("checkpointer_init_failed_checkpoint_disabled")
            return None
        _cm = cm
        _checkpointer = saver
        logger.info("checkpointer_ready")
    return _checkpointer


async def close_checkpointer() -> None:
    global _checkpointer, _cm
    if _cm is not None:
        try:
            await _cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            logger.warning("checkpointer_close_failed")
    _checkpointer = None
    _cm = None

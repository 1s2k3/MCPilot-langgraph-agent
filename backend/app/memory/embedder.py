"""Embedding 层：默认本地 fastembed（sentence-transformers/all-MiniLM-L6-v2，384 维 ONNX）。

- 零外部 API 依赖；接口可插拔（EMBEDDING_PROVIDER=disabled 时整体禁用长期记忆检索）
- fastembed 是同步库，统一走 asyncio.to_thread 防阻塞事件循环
"""

import asyncio
import threading

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_embedder = None
_lock = threading.Lock()
_init_error = False


def get_embedder():
    """懒加载模型（首次调用下载/加载约数秒）。disabled → None。"""
    global _embedder, _init_error
    s = get_settings()
    if s.embedding_provider == "disabled":
        return None
    if _init_error:
        return None
    if _embedder is not None:
        return _embedder
    with _lock:
        if _embedder is not None:
            return _embedder
        if _init_error:
            return None
        try:
            from fastembed import TextEmbedding

            kwargs = {"model_name": s.embedding_model}
            if s.embedding_cache_dir:
                kwargs["cache_dir"] = s.embedding_cache_dir
            _embedder = TextEmbedding(**kwargs)
            logger.info("embedder_loaded", model=s.embedding_model)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedder_init_failed", error=str(exc)[:200])
            _init_error = True
            return None
    return _embedder


def embed(texts: list[str]) -> list[list[float]] | None:
    """同步 embed；返回与输入等长的向量列表（provider 禁用时 None）。"""
    emb = get_embedder()
    if emb is None:
        return None
    return [v.tolist() for v in emb.embed(texts)]


async def aembed(texts: list[str]) -> list[list[float]] | None:
    return await asyncio.to_thread(embed, texts)

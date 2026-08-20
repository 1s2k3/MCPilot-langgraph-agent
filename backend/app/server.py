"""uvicorn 自定义事件循环工厂。

psycopg3 异步（checkpointer / SQLAlchemy）在 Windows 上要求 Selector 事件循环，
而 uvicorn 0.52+ 在 Windows 默认使用 Proactor。启动方式：

    uvicorn app.main:app --loop app.server:loop_factory

Linux（生产容器）返回 None → uvicorn 默认行为，不受影响。
"""
import asyncio
import sys


def loop_factory(use_subprocess: bool = False):  # noqa: FBT001, FBT002
    """注意：uvicorn 将本函数直接交给 asyncio.Runner，须返回事件循环实例（非类）。"""
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return None

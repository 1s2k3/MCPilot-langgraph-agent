"""pytest-asyncio 事件循环策略：Windows 使用 SelectorEventLoop。

psycopg async 依赖非 Proactor 事件循环（uvicorn 侧已在 server.loop_factory 处理，
测试侧经此 fixture 对齐，避免 Windows 上数据库访问类测试崩溃）。
"""

import asyncio
import sys

import pytest


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy | None:
    if sys.platform == "win32":

        class _SelectorPolicy(asyncio.DefaultEventLoopPolicy):
            def new_event_loop(self) -> asyncio.AbstractEventLoop:
                return asyncio.SelectorEventLoop()

        return _SelectorPolicy()
    return None

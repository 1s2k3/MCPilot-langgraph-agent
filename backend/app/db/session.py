"""异步引擎与会话工厂。

Windows 注意：psycopg3 异步依赖 selector 事件循环，必须在创建事件循环前设置策略
（uvicorn / pytest-asyncio 创建循环时都会读取策略）。
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

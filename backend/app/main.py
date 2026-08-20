"""FastAPI 入口。

启动顺序（lifespan）：
1. 日志初始化 + LangSmith 环境配置
2. 并行：MCP 客户端会话池 + LangGraph Postgres Checkpointer（均降级容错）

迁移由部署脚本执行：alembic upgrade head（容器 entrypoint / 本地手动）。
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError

from app.agent.checkpoint import close_checkpointer, get_checkpointer
from app.api.agents import router as agents_router
from app.api.eval import router as eval_router
from app.api.health import router as health_router
from app.api.keys import router as keys_router
from app.api.mcp import router as mcp_router
from app.api.memory import router as memory_router
from app.api.runs import alias_router as runs_alias_router
from app.api.runs import router as runs_router
from app.api.threads import router as threads_router
from app.api.tools import router as tools_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger, setup_logging
from app.core.tracing import configure_tracing
from app.tools.mcp_client import mcp_manager

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    configure_tracing(settings)
    # 日志不打印凭据，只打印主机部分
    logger.info("startup", database_host=settings.database_url.split("@")[-1])
    # MCP 会话池与 checkpointer 并行初始化；两者均内部降级，不阻断启动
    results = await asyncio.gather(
        mcp_manager.refresh(), get_checkpointer(), return_exceptions=True
    )
    if isinstance(results[0], Exception):
        logger.warning("mcp_refresh_failed_on_startup", error=str(results[0])[:200])
    yield
    await close_checkpointer()
    logger.info("shutdown")


app = FastAPI(title="MCP Agent Platform", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
app.include_router(threads_router, prefix="/api")
app.include_router(runs_router, prefix="/api")
app.include_router(runs_alias_router, prefix="/api")
app.include_router(mcp_router, prefix="/api")
app.include_router(tools_router, prefix="/api")
app.include_router(memory_router, prefix="/api")
app.include_router(keys_router, prefix="/api")
app.include_router(eval_router, prefix="/api")


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """统一错误信封（API 文档契约，见 docs/development-framework.md §7）。"""
    return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底：数据库不可达 → 503 明确信封；其余 → 500 内部错误（不泄漏堆栈）。"""
    if isinstance(exc, (DBAPIError, InterfaceError)) or "psycopg" in type(exc).__module__:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "database_unavailable",
                    "message": "数据库不可用，请检查 PostgreSQL 连接",
                    "retryable": True,
                    "details": {},
                }
            },
        )
    logger.exception("unhandled_error", path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": str(exc)[:300] or "内部错误",
                "retryable": False,
                "details": {},
            }
        },
    )

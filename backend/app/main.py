"""FastAPI 入口。

启动顺序（lifespan）：
1. 日志初始化
2. （M2）MCP 客户端会话池；LangSmith 环境配置
3. （M3）LangGraph Postgres Checkpointer setup

迁移由部署脚本执行：alembic upgrade head（容器 entrypoint / 本地手动）。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.checkpoint import close_checkpointer, get_checkpointer
from app.api.agents import router as agents_router
from app.api.health import router as health_router
from app.api.keys import router as keys_router
from app.api.mcp import router as mcp_router
from app.api.memory import router as memory_router
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
    try:
        await mcp_manager.refresh()  # MCP 失联降级，不阻断启动
    except Exception:  # noqa: BLE001
        logger.warning("mcp_refresh_failed_on_startup")
    await get_checkpointer()  # 初始化失败内部降级（checkpoint 禁用），不阻断启动
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
app.include_router(mcp_router, prefix="/api")
app.include_router(tools_router, prefix="/api")
app.include_router(memory_router, prefix="/api")
app.include_router(keys_router, prefix="/api")


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """统一错误信封（API 文档契约，见 docs/development-framework.md §7）。"""
    return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())

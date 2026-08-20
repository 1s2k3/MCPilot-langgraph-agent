"""MCP 客户端管理（§5.2）：MultiServerMCPClient 会话池 + 健康状态 + 工具注册表集成。

设计约定：
- 应用级单例 mcp_manager，启动时 refresh，配置变更后由 API 触发 refresh
- server 失联 → 该 server 工具隐藏 + health 标记 error，不影响其他 server 与平台运行（降级）
- 工具名带 server 前缀（tool_name_prefix=True），同名工具不冲突，前端按 server 分组
"""

import asyncio
import json
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import McpServer
from app.db.session import SessionLocal
from app.security.keys import decrypt_secret
from app.tools.registry import ToolMeta

logger = get_logger(__name__)

_DEMO_PATH = Path(__file__).resolve().parents[3] / "mcp-demo" / "index.js"


def build_connection_config(row: McpServer) -> dict:
    """DB 行 → langchain-mcp-adapters 连接配置。"""
    if row.transport == "stdio":
        if not row.command:
            raise ValueError(f"stdio 传输需要 command: {row.name}")
        cfg: dict = {"transport": "stdio", "command": row.command, "args": row.args or []}
        if row.env_encrypted:
            cfg["env"] = json.loads(decrypt_secret(row.env_encrypted))
        elif row.env:  # 兼容迁移前的明文遗留
            cfg["env"] = row.env
        return cfg
    if row.transport == "streamable_http":
        if not row.url:
            raise ValueError(f"streamable_http 传输需要 url: {row.name}")
        cfg = {"transport": "streamable_http", "url": row.url}
        if row.headers_encrypted:
            cfg["headers"] = json.loads(decrypt_secret(row.headers_encrypted))
        return cfg
    raise ValueError(f"不支持的传输类型: {row.transport}")


class McpManager:
    def __init__(self) -> None:
        self._client: MultiServerMCPClient | None = None
        self._tools: dict[str, ToolMeta] = {}
        self._health: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def tools(self) -> dict[str, ToolMeta]:
        return dict(self._tools)

    def health(self) -> dict[str, str]:
        return dict(self._health)

    async def refresh(self) -> None:
        """从 DB 重载启用中的 MCP 服务器，重建会话池与工具注册表。

        langchain-mcp-adapters 0.3.x 的 MultiServerMCPClient 不支持上下文管理器：
        每次工具调用开启独立会话（stateless），无显式 close，旧 client 直接丢弃即可。
        """
        async with self._lock:
            new_tools: dict[str, ToolMeta] = {}
            health: dict[str, str] = {}
            async with SessionLocal() as session:
                await self._seed_demo(session)
                rows = list(
                    (await session.execute(select(McpServer).where(McpServer.enabled))).scalars()
                )
            if not rows:
                self._client, self._tools, self._health = None, {}, {}
                return
            client = MultiServerMCPClient(
                {row.name: build_connection_config(row) for row in rows},
                tool_name_prefix=True,
            )
            for row in rows:
                try:
                    allowlist = set(row.tool_allowlist) if row.tool_allowlist else None
                    for tool in await client.get_tools(server_name=row.name):
                        if allowlist is not None and tool.name not in allowlist:
                            continue
                        new_tools[tool.name] = ToolMeta(
                            tool=tool, server=row.name, render_hint="json", source="mcp"
                        )
                    health[row.name] = "ok"
                except Exception as exc:  # noqa: BLE001
                    logger.warning("mcp_server_load_failed", server=row.name, error=str(exc)[:200])
                    health[row.name] = "error"
            self._client = client
            self._tools = new_tools
            self._health = health

    async def test_server(self, row: McpServer) -> list[dict]:
        """连接测试：临时客户端列工具清单（不触碰主会话池）。"""
        client = MultiServerMCPClient({row.name: build_connection_config(row)})
        tools = await client.get_tools(server_name=row.name)
        return [{"name": t.name, "description": t.description or ""} for t in tools]

    async def _seed_demo(self, session) -> None:
        """首次启动播种演示 server：配置了 MCP_DEMO_URL 用 HTTP，否则 stdio 拉起本地 node。"""
        s = get_settings()
        if not s.seed_demo_mcp:
            return
        exists = (
            await session.execute(select(McpServer).where(McpServer.name == "mcp-demo"))
        ).scalar_one_or_none()
        if exists is not None:
            return
        if s.mcp_demo_url:
            row = McpServer(name="mcp-demo", transport="streamable_http", url=s.mcp_demo_url)
        else:
            row = McpServer(
                name="mcp-demo", transport="stdio", command="node", args=[str(_DEMO_PATH)]
            )
        session.add(row)
        await session.commit()


mcp_manager = McpManager()

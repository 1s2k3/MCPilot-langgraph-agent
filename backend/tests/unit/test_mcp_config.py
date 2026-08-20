"""MCP 连接配置构建单元测试（不启动真实 server）。"""

import pytest

from app.db.models import McpServer
from app.tools.mcp_client import build_connection_config


def test_stdio_config() -> None:
    row = McpServer(name="demo", transport="stdio", command="node", args=["index.js"])
    cfg = build_connection_config(row)
    assert cfg == {"transport": "stdio", "command": "node", "args": ["index.js"]}


def test_stdio_requires_command() -> None:
    row = McpServer(name="demo", transport="stdio", command=None)
    with pytest.raises(ValueError, match="command"):
        build_connection_config(row)


def test_http_requires_url() -> None:
    row = McpServer(name="demo", transport="streamable_http", url=None)
    with pytest.raises(ValueError, match="url"):
        build_connection_config(row)


def test_unsupported_transport() -> None:
    row = McpServer(name="demo", transport="sse", url="http://x")
    with pytest.raises(ValueError, match="传输类型"):
        build_connection_config(row)

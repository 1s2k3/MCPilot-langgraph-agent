"""MCP 连接配置构建单元测试（不启动真实 server）。"""

import pytest

from app.api.mcp import McpServerCreate, _validate
from app.core.errors import AppError
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


def test_validate_rejects_non_allowlisted_command() -> None:
    body = McpServerCreate(name="x", transport="stdio", command="bash")
    with pytest.raises(AppError, match="允许列表"):
        _validate(body)
    # 白名单内命令通过
    assert _validate(McpServerCreate(name="x", transport="stdio", command="node")) is None


def test_validate_rejects_private_and_bad_scheme_urls() -> None:
    with pytest.raises(AppError, match="私有|保留"):
        _validate(
            McpServerCreate(name="x", transport="streamable_http", url="http://127.0.0.1:8001/mcp")
        )
    with pytest.raises(AppError, match="http"):
        _validate(McpServerCreate(name="x", transport="streamable_http", url="file:///etc/passwd"))

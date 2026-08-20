"""M2 验收：MCP 接入全链路（真实 mcp-demo server，真实 PG，ScriptedLLM）。

- HTTP 传输（跨平台，本地 Windows 也可跑）：注册 → 连接测试 → 工具执行 → run 共存
- stdio 传输：仅 Linux/CI（Windows 下 asyncio selector 循环不支持子进程）
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_DEMO_DIR = Path(__file__).resolve().parents[3] / "mcp-demo"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_port(port: int, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.2)
    raise AssertionError(f"mcp-demo 未在 {timeout_s}s 内监听端口 {port}")


@pytest.fixture()
async def demo_http_server():
    """后台启动 mcp-demo HTTP 模式（同步 subprocess，与事件循环策略无关）。"""
    port = _free_port()
    # 刻意用同步 Popen：Windows 下 selector 事件循环不支持异步子进程
    proc = subprocess.Popen(  # noqa: ASYNC220
        ["node", "index.js"],
        cwd=_DEMO_DIR,
        env={**os.environ, "MCP_DEMO_PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await _wait_port(port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


async def test_mcp_server_crud_and_tool_execution(db_available, demo_http_server) -> None:
    from app.main import app
    from app.tools.executor import normalize_result
    from app.tools.mcp_client import mcp_manager

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 注册
        resp = await client.post(
            "/api/mcp-servers",
            json={"name": "demo-http", "transport": "streamable_http", "url": demo_http_server},
        )
        assert resp.status_code == 201, resp.text
        server_id = resp.json()["id"]

        # 连接测试：列出工具
        resp = await client.post(f"/api/mcp-servers/{server_id}/test")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True, body
        names = {t["name"] for t in body["tools"]}
        assert {"math_add", "math_multiply", "get_time", "echo"} <= names

        # 工具清单聚合（带前缀）
        resp = await client.get("/api/tools")
        items = {t["name"]: t for t in resp.json()["tools"]}
        assert "demo-http_math_add" in items
        assert items["demo-http_math_add"]["server"] == "demo-http"
        assert items["demo-http_math_add"]["source"] == "mcp"

        # 会话池已刷新：直接执行 MCP 工具（走真实 MCP 协议往返）
        assert mcp_manager.health().get("demo-http") == "ok"
        meta = mcp_manager.tools()["demo-http_math_add"]
        result = await meta.tool.ainvoke({"a": 20, "b": 22})
        assert normalize_result(result)["data"] == "42"

        # 删除 → 工具移除
        resp = await client.delete(f"/api/mcp-servers/{server_id}")
        assert resp.status_code == 204
        assert "demo-http_math_add" not in mcp_manager.tools()


async def test_run_coexists_with_mcp_tools(db_available, demo_http_server) -> None:
    """MCP 工具绑定不影响普通 run（ScriptedLLM 走本地 calculator）。"""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/mcp-servers",
            json={"name": "demo-http", "transport": "streamable_http", "url": demo_http_server},
        )
        assert resp.status_code == 201, resp.text
        resp = await client.post("/api/agents", json={"name": "MCP 共存测试"})
        agent_id = resp.json()["id"]
        resp = await client.post("/api/threads", json={"agent_id": agent_id})
        thread_id = resp.json()["id"]
        resp = await client.post(f"/api/threads/{thread_id}/runs", json={"input": "帮我计算 1+2"})
        run_id = resp.json()["run_id"]

        deadline = time.monotonic() + 15
        status = None
        while time.monotonic() < deadline:
            run = (await client.get(f"/api/threads/{thread_id}/runs/{run_id}")).json()
            status = run["status"]
            if status in ("completed", "failed", "cancelled"):
                break
            await asyncio.sleep(0.2)
        assert status == "completed", run
        assert "3" in run["final_answer"]
        # 清理：避免 demo-http 注册残留影响其他测试文件
        servers = (await client.get("/api/mcp-servers")).json()
        for srv in servers:
            if srv["name"] == "demo-http":
                await client.delete(f"/api/mcp-servers/{srv['id']}")


@pytest.mark.skipif(
    sys.platform == "win32", reason="stdio 子进程依赖 Proactor 循环，CI(Linux) 覆盖"
)
async def test_stdio_transport_roundtrip(db_available) -> None:
    from app.db.models import McpServer
    from app.tools.mcp_client import build_connection_config, mcp_manager

    row = McpServer(
        name="demo-stdio",
        transport="stdio",
        command="node",
        args=[str(_DEMO_DIR / "index.js")],
    )
    row.id = None  # 仅配置构建，不落库
    cfg = build_connection_config(row)
    assert cfg["transport"] == "stdio"

    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient({"demo-stdio": cfg}, tool_name_prefix=True)
    tools = await client.get_tools(server_name="demo-stdio")
    names = {t.name for t in tools}
    assert "demo-stdio_math_add" in names
    tool = next(t for t in tools if t.name == "demo-stdio_math_add")
    out = await tool.ainvoke({"a": 1, "b": 2})
    from app.tools.executor import normalize_result

    assert normalize_result(out)["data"] == "3"
    assert "demo-stdio" not in mcp_manager.tools()

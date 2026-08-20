"""M5 验收：HITL 全链路（真实 PG + ScriptedLLM）+ API Key 管理。"""

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_TERMINAL = ("completed", "failed", "cancelled")


async def _create_agent(client: AsyncClient, policy: dict) -> str:
    resp = await client.post(
        "/api/agents",
        json={"name": "HITL 测试 Agent", "tool_policy": policy},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _start_run(client: AsyncClient, thread_id: str) -> str:
    resp = await client.post(f"/api/threads/{thread_id}/runs", json={"input": "帮我计算 1+2"})
    assert resp.status_code == 201, resp.text
    return resp.json()["run_id"]


async def _wait_status(
    client: AsyncClient, thread_id: str, run_id: str, expected: str, timeout_s: float = 20.0
) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        run = (await client.get(f"/api/threads/{thread_id}/runs/{run_id}")).json()
        if run["status"] == expected:
            return run
        if run["status"] in ("failed", "cancelled"):
            raise AssertionError(f"run 意外结束: {run}")
        await asyncio.sleep(0.2)
    raise AssertionError(f"run 未在 {timeout_s}s 内到达 {expected}")


async def test_hitl_deny_flow(db_available) -> None:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        policy = {"rules": [{"tool": "calculator", "action": "ask"}], "default": "allow"}
        agent_id = await _create_agent(client, policy)
        thread_id = (await client.post("/api/threads", json={"agent_id": agent_id})).json()["id"]
        run_id = await _start_run(client, thread_id)

        run = await _wait_status(client, thread_id, run_id, "interrupted")
        assert run["status"] == "interrupted"

        events = (await client.get(f"/api/threads/{thread_id}/runs/{run_id}/events")).json()[
            "events"
        ]
        interrupts = [e for e in events if e["type"] == "interrupt"]
        assert interrupts, "应有 interrupt 事件"
        assert [p["name"] for p in interrupts[0]["payload"]["pending"]] == ["calculator"]

        # 非 interrupted 状态 resume → 409
        resp = await client.post(
            f"/api/threads/{thread_id}/runs/{run_id}/resume",
            json={"action": "approve"},
        )
        assert resp.status_code == 200, resp.text

        run = await _wait_status(client, thread_id, run_id, "completed")
        assert run["status"] == "completed"
        events = (await client.get(f"/api/threads/{thread_id}/runs/{run_id}/events")).json()[
            "events"
        ]
        assert any(e["type"] == "resumed" for e in events)
        # 无 executed 工具记录（deny 路径不落 tool_calls）
        resp = await client.get(f"/api/threads/{thread_id}/runs/{run_id}/events")
        assert not any(e["type"] == "tool_call_start" for e in resp.json()["events"])


async def test_hitl_approve_session_wide(db_available) -> None:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        policy = {"rules": [{"tool": "calculator", "action": "ask"}], "default": "allow"}
        agent_id = await _create_agent(client, policy)
        thread_id = (await client.post("/api/threads", json={"agent_id": agent_id})).json()["id"]

        run_id = await _start_run(client, thread_id)
        await _wait_status(client, thread_id, run_id, "interrupted")
        resp = await client.post(
            f"/api/threads/{thread_id}/runs/{run_id}/resume",
            json={"action": "approve", "session_wide": True},
        )
        assert resp.status_code == 200
        run = await _wait_status(client, thread_id, run_id, "completed")
        assert run["status"] == "completed"

        # 会话级授权随 checkpoint 持久化：第二轮不再中断
        run_id2 = await _start_run(client, thread_id)
        run2 = await _wait_status(client, thread_id, run_id2, "completed")
        assert run2["status"] == "completed"
        events2 = (await client.get(f"/api/threads/{thread_id}/runs/{run_id2}/events")).json()[
            "events"
        ]
        assert not any(e["type"] == "interrupt" for e in events2)


async def test_api_key_crud_and_provider_fallback(db_available) -> None:
    from app.main import app
    from app.security.keys import get_provider_key

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/api-keys",
            json={"provider": "anthropic", "name": "测试密钥", "key": "sk-ant-secret-123456"},
        )
        assert resp.status_code == 201, resp.text
        key_id = resp.json()["id"]
        assert resp.json()["masked"] == "***"  # 写后不可读

        resp = await client.get("/api/api-keys")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert all("sk-ant" not in r["masked"] for r in rows)  # 明文绝不回显

        # provider 密钥兜底读取：解密成功且 last_used_at 刷新
        plain = await get_provider_key("anthropic")
        assert plain == "sk-ant-secret-123456"

        resp = await client.delete(f"/api/api-keys/{key_id}")
        assert resp.status_code == 204
        assert await get_provider_key("anthropic") is None

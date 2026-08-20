"""M1 验收：完整 run 生命周期（ScriptedLLM 驱动，真实 PG）。

覆盖：API 创建 agent/thread/run → 后台执行 → 事件完整落库 → SSE 流端到端 → 消息落库。
"""

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_TERMINAL = ("completed", "failed", "cancelled")


async def _create_agent(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/agents",
        json={
            "name": "测试 Agent",
            "system_prompt": "你是一个测试助手，计算类问题请用 calculator 工具。",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _wait_run_done(
    client: AsyncClient, thread_id: str, run_id: str, timeout_s: float = 15.0
) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        resp = await client.get(f"/api/threads/{thread_id}/runs/{run_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in _TERMINAL:
            return body
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"run 未在 {timeout_s}s 内结束: {body}")
        await asyncio.sleep(0.2)


async def test_full_run_with_scripted_llm(db_available) -> None:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        agent_id = await _create_agent(client)
        resp = await client.post("/api/threads", json={"agent_id": agent_id})
        thread_id = resp.json()["id"]

        resp = await client.post(f"/api/threads/{thread_id}/runs", json={"input": "帮我计算 1+2"})
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["run_id"]

        body = await _wait_run_done(client, thread_id, run_id)
        assert body["status"] == "completed", body
        assert "3" in body["final_answer"]

        # 事件落库完整且有序
        resp = await client.get(f"/api/threads/{thread_id}/runs/{run_id}/events")
        events = resp.json()["events"]
        types = [e["type"] for e in events]
        assert types[0] == "run_start"
        assert types[-1] == "run_end"
        assert "tool_call_start" in types
        assert "tool_call_end" in types
        assert "llm_delta" in types
        seqs = [e["seq"] for e in events]
        assert seqs == sorted(seqs)

        # 消息落库：user + assistant
        resp = await client.get(f"/api/threads/{thread_id}/messages")
        messages = resp.json()
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "帮我计算 1+2"


async def test_run_stream_sse_end_to_end(db_available) -> None:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        agent_id = await _create_agent(client)
        resp = await client.post("/api/threads", json={"agent_id": agent_id})
        thread_id = resp.json()["id"]
        resp = await client.post(f"/api/threads/{thread_id}/runs", json={"input": "帮我计算 1+2"})
        run_id = resp.json()["run_id"]

        received: list[dict] = []
        async with client.stream("GET", f"/api/threads/{thread_id}/runs/{run_id}/stream") as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = json.loads(line[len("data:") :].strip())
                received.append(data)
                if data["type"] == "run_end":
                    break
        types = [e["type"] for e in received]
        assert types[0] == "run_start"
        assert types[-1] == "run_end"
        assert "tool_call_end" in types


async def test_concurrent_run_rejected(db_available) -> None:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        agent_id = await _create_agent(client)
        resp = await client.post("/api/threads", json={"agent_id": agent_id})
        thread_id = resp.json()["id"]
        resp = await client.post(f"/api/threads/{thread_id}/runs", json={"input": "第一个"})
        assert resp.status_code == 201
        resp2 = await client.post(f"/api/threads/{thread_id}/runs", json={"input": "第二个"})
        assert resp2.status_code == 409
        assert resp2.json()["error"]["code"] == "run_in_progress"

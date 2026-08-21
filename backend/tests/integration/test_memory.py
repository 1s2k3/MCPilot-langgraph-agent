"""M3 验收：Checkpoint 持久化 + 长期记忆全链路（真实 PG + ScriptedLLM + 本地 embedding）。"""

import asyncio
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_TERMINAL = ("completed", "failed", "cancelled")


async def _create_agent(client: AsyncClient) -> str:
    resp = await client.post("/api/agents", json={"name": "M3 测试 Agent"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _run(client: AsyncClient, thread_id: str, text: str) -> dict:
    resp = await client.post(f"/api/threads/{thread_id}/runs", json={"input": text})
    assert resp.status_code == 201, resp.text
    run_id = resp.json()["run_id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        run = (await client.get(f"/api/threads/{thread_id}/runs/{run_id}")).json()
        if run["status"] in _TERMINAL:
            return run
        await asyncio.sleep(0.2)
    raise AssertionError(f"run 未结束: {run}")


async def test_multi_turn_checkpoint_persistence(db_available) -> None:
    """多轮对话：线程历史经 checkpoint 恢复；消息表累计；checkpoint 快照可读。"""
    from app.agent.checkpoint import get_checkpointer
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        agent_id = await _create_agent(client)
        thread_id = (await client.post("/api/threads", json={"agent_id": agent_id})).json()["id"]

        run1 = await _run(client, thread_id, "帮我计算 1+2")
        assert run1["status"] == "completed"
        run2 = await _run(client, thread_id, "再帮我算一次")
        assert run2["status"] == "completed"

        messages = (await client.get(f"/api/threads/{thread_id}/messages")).json()
        assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]

        # 真实 checkpoint 快照：线程状态含 4 条消息 + 计数
        ck = await get_checkpointer()
        assert ck is not None
        snap = await ck.aget_tuple({"configurable": {"thread_id": thread_id}})
        channel_values = snap.checkpoint.get("channel_values", {})
        msgs = channel_values.get("messages", [])
        assert len(msgs) >= 4
        assert channel_values.get("iteration_count", 0) >= 2


async def test_memory_store_retrieve_delete(db_available) -> None:
    from app.memory.extractor import MemoryItem, store_single_memory
    from app.memory.retriever import delete_top_match, retrieve_memories

    n = await store_single_memory(
        None, None, MemoryItem(type="preference", content="用户最喜欢的颜色是蓝色", importance=0.9)
    )
    assert n == 1
    rows = await retrieve_memories("用户喜欢什么颜色", k=3)
    assert rows, "检索无结果（embedding 或 pgvector 异常）"
    assert rows[0]["content"] == "用户最喜欢的颜色是蓝色"
    assert rows[0]["similarity"] > 0.3

    removed = await delete_top_match("最喜欢的颜色", thread_id=None, threshold=0.3)
    assert removed == "用户最喜欢的颜色是蓝色"


async def test_extraction_with_dedupe(db_available) -> None:
    from app.llm.scripted import ScriptedChatModel
    from app.memory.extractor import MemoryExtraction, MemoryItem, store_extracted_memories

    item = MemoryExtraction(
        memories=[MemoryItem(type="fact", content="用户的名字是小明", importance=0.8)]
    )
    llm = ScriptedChatModel(responses=[item, item])
    written = await store_extracted_memories(None, None, llm, "用户: 我叫小明")
    assert written == 1
    written2 = await store_extracted_memories(None, None, llm, "用户: 我叫小明")
    assert written2 == 0


async def test_remember_tool_and_memory_injection(db_available) -> None:
    """remember_memory 工具全流程：工具调用 → 记忆落库 → 后续 run 检索注入（notice 事件）。"""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        agent_id = await _create_agent(client)
        thread_id = (await client.post("/api/threads", json={"agent_id": agent_id})).json()["id"]

        run1 = await _run(client, thread_id, "记住 我最喜欢的颜色是蓝色")
        assert run1["status"] == "completed", run1

        events = (await client.get(f"/api/threads/{thread_id}/runs/{run1['id']}/events")).json()[
            "events"
        ]
        tool_names = [e["payload"]["name"] for e in events if e["type"] == "tool_call_start"]
        assert "remember_memory" in tool_names

        memories = (await client.get(f"/api/threads/{thread_id}/memories")).json()["memories"]
        assert any("蓝色" in m["content"] for m in memories), memories

        # 第二轮：load_context 检索到记忆 → notice 事件
        run2 = await _run(client, thread_id, "你好")
        events2 = (await client.get(f"/api/threads/{thread_id}/runs/{run2['id']}/events")).json()[
            "events"
        ]
        notices = [e for e in events2 if e["type"] == "notice"]
        retrieved = sum(e["payload"].get("memories_retrieved", 0) for e in notices)
        assert retrieved >= 1, events2

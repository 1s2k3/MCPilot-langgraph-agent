"""事件总线单元测试（persist=False，不依赖数据库）。"""

import asyncio
import uuid

import pytest

from app.events.bus import EventBus


async def test_publish_delivers_in_seq_order() -> None:
    bus = EventBus(uuid.uuid4(), persist=False)
    q = bus.subscribe()
    for i in range(5):
        await bus.publish(bus.next_event("notice", {"i": i}))
    received = [q.get_nowait() for _ in range(5)]
    assert [e.seq for e in received] == [1, 2, 3, 4, 5]
    assert all(e.type == "notice" for e in received)
    await bus.close()


async def test_close_is_idempotent_and_unsubscribes() -> None:
    bus = EventBus(uuid.uuid4(), persist=False)
    q = bus.subscribe()
    await bus.publish(bus.next_event("notice"))
    assert q.get_nowait().seq == 1
    await bus.close()
    await bus.close()
    # 关闭后不再推送
    await bus.publish(bus.next_event("notice"))
    with pytest.raises(asyncio.QueueEmpty):
        q.get_nowait()

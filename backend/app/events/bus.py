"""事件总线：SSE 实时推送 + Postgres 持久化（Timeline 回放数据源）。

设计约定：
- publish 不阻塞运行：DB 落库由独立写任务消费队列，失败仅记日志
- 订阅者队列有界：慢客户端丢弃最旧事件，防止拖垮 run
- seq 单调递增，SSE 断线重连以 Last-Event-ID(=seq) 幂等补拉
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.db.models import Event as EventRow
from app.db.session import SessionLocal
from app.events.models import Event

logger = get_logger(__name__)

_SUBSCRIBER_MAX = 2048
_FLUSH_TIMEOUT_S = 10
# 关闭哨兵：放入持久化队列，写任务消费到即退出（保证哨兵前的事件全部落库）
_SENTINEL = object()


class EventBus:
    def __init__(self, run_id: uuid.UUID, *, persist: bool = True) -> None:
        self.run_id = run_id
        self.persist = persist
        self.closed = False
        self._seq = 0
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._db_queue: asyncio.Queue[Event] = asyncio.Queue()
        self._writer: asyncio.Task | None = None
        if persist:
            self._writer = asyncio.create_task(self._write_loop(), name=f"event-writer-{run_id}")

    def next_event(self, type_: str, payload: dict | None = None) -> Event:
        self._seq += 1
        return Event(
            seq=self._seq,
            run_id=str(self.run_id),
            type=type_,
            payload=payload or {},
            ts=datetime.now(UTC).isoformat(),
        )

    async def publish(self, event: Event) -> Event:
        """发布事件：入持久化队列 + 推送给所有订阅者（不阻塞）。"""
        if self.closed:
            return event
        if self.persist:
            await self._db_queue.put(event)
        for q in list(self._subscribers):
            if q.qsize() >= _SUBSCRIBER_MAX:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(event)
        return event

    def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(q)

    async def _write_loop(self) -> None:
        while True:
            evt = await self._db_queue.get()
            if evt is _SENTINEL:
                return
            await self._persist_one(evt)

    async def _persist_one(self, evt: Event) -> None:
        try:
            async with SessionLocal() as session:
                session.add(
                    EventRow(run_id=self.run_id, seq=evt.seq, type=evt.type, payload=evt.payload)
                )
                await session.commit()
        except Exception:
            logger.exception(
                "event_persist_failed", run_id=str(self.run_id), seq=evt.seq, type=evt.type
            )

    async def close(self) -> None:
        """关闭总线：哨兵唤醒写任务，等其按序写完队列中全部事件后退出。

        避免旧实现的竞态：close 与写任务并发消费队列，写任务正写最后一个
        事件（如 run_end）时 close 误判队列为空并 cancel 写任务 → 事件丢失。
        """
        if self.closed:
            return
        self.closed = True
        if self.persist:
            try:
                async with asyncio.timeout(_FLUSH_TIMEOUT_S):
                    await self._db_queue.put(_SENTINEL)
                    if self._writer is not None:
                        await self._writer
            except TimeoutError:
                logger.warning("event_flush_timeout", run_id=str(self.run_id))
                if self._writer is not None:
                    self._writer.cancel()
        self._subscribers.clear()

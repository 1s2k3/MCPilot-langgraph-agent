"""运行管理：后台任务执行图、事件总线注册、SSE 回放、取消。"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.agent.graph import GraphContext, build_graph
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.models import Agent, Message, Run
from app.db.models import Event as EventRow
from app.db.session import SessionLocal
from app.events.bus import EventBus
from app.events.models import Event
from app.llm.anthropic import build_anthropic_model
from app.llm.base import resolve_llm_config
from app.llm.scripted import ScriptedChatModel, demo_responder
from app.tools.mcp_client import mcp_manager
from app.tools.registry import build_registry

logger = get_logger(__name__)

_TERMINAL = ("completed", "failed", "cancelled")


def build_executor_llm(agent_row: Agent) -> object:
    """executor 节点 LLM：无 API Key 或显式 scripted 模式时用脚本化假 LLM。"""
    s = get_settings()
    if s.scripted_llm or s.anthropic_api_key is None:
        if not s.scripted_llm:
            logger.warning("no_anthropic_key_using_scripted")
        return ScriptedChatModel(responder=demo_responder)
    cfg = resolve_llm_config("executor", (agent_row.node_models or {}).get("executor"))
    return build_anthropic_model(cfg)


async def _write_message(
    session, thread_id: uuid.UUID, run_id: uuid.UUID, role: str, content: str
) -> None:
    seq = (
        await session.execute(
            select(func.coalesce(func.max(Message.seq), 0)).where(Message.thread_id == thread_id)
        )
    ).scalar_one() + 1
    session.add(Message(thread_id=thread_id, run_id=run_id, role=role, content=content, seq=seq))


async def _fail_run(
    run: Run, bus: EventBus, *, code: str, message: str, exc: Exception | None = None
) -> None:
    now = datetime.now(UTC)
    run.status = "failed"
    run.error = {"code": code, "message": message}
    run.finished_at = now
    await bus.publish(bus.next_event("error", {"code": code, "message": message, "terminal": True}))
    await bus.publish(bus.next_event("run_end", {"status": "failed", "error": run.error}))


async def execute_run(
    run_id: uuid.UUID,
    thread_id: uuid.UUID,
    agent: Agent,
    user_input: str,
    bus: EventBus,
) -> None:
    """一次运行的主流程（后台任务）。"""
    s = get_settings()
    started = time.perf_counter()
    budgets = {
        "max_llm_calls": s.max_llm_calls,
        "max_total_tool_calls": s.max_total_tool_calls,
        **(agent.budgets or {}),
    }
    registry = build_registry()
    for name, meta in mcp_manager.tools().items():  # 注入 MCP 工具（带 server 前缀）
        registry.register(name, meta)
    llm = build_executor_llm(agent)

    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        if run is None:
            logger.error("run_row_missing", run_id=str(run_id))
            return
        try:
            await bus.publish(
                bus.next_event(
                    "run_start",
                    {
                        "thread_id": str(thread_id),
                        "agent_id": str(agent.id),
                        "agent": agent.name,
                        "input": user_input,
                    },
                )
            )
            run.status = "running"
            await session.commit()

            ctx = GraphContext(
                llm_executor=llm,
                registry=registry,
                system_prompt=agent.system_prompt or "",
                bus=bus,
                run_id=run_id,
                thread_id=thread_id,
                budgets=budgets,
                session=session,
            )
            graph = build_graph(ctx)
            config = {"configurable": {"thread_id": str(thread_id)}}
            async with asyncio.timeout(s.run_timeout_seconds):
                # 事件由节点内经 bus 直接发布（SSE + 落库），此处消费 custom 通道即可
                async for _ in graph.astream(
                    {"messages": [("user", user_input)]}, config=config, stream_mode="custom"
                ):
                    pass

            final_state = await graph.aget_state(config)
            values = final_state.values or {}
            answer = values.get("final_answer")
            if not answer:
                msgs = values.get("messages") or []
                last = msgs[-1] if msgs else None
                answer = last.content if last is not None and isinstance(last.content, str) else ""
            run.status = "completed"
            run.final_answer = answer
            run.latency_ms = int((time.perf_counter() - started) * 1000)
            run.usage = values.get("usage_total") or {}
            run.finished_at = datetime.now(UTC)
            await _write_message(session, thread_id, run_id, "assistant", answer)
            await session.commit()
            await bus.publish(
                bus.next_event(
                    "run_end",
                    {
                        "status": "completed",
                        "final_answer": answer,
                        "latency_ms": run.latency_ms,
                        "usage": run.usage,
                    },
                )
            )
        except asyncio.CancelledError:
            run.status = "cancelled"
            run.finished_at = datetime.now(UTC)
            await session.commit()
            try:
                await bus.publish(bus.next_event("run_end", {"status": "cancelled"}))
            except Exception:  # noqa: BLE001
                pass
            raise
        except TimeoutError:
            await _fail_run(
                run, bus, code="run_timeout", message=f"运行超时（{s.run_timeout_seconds}s）"
            )
            await session.commit()
        except AppError as exc:
            await _fail_run(run, bus, code=exc.code, message=exc.message)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("run_failed", run_id=str(run_id))
            await _fail_run(run, bus, code="internal_error", message=str(exc)[:500])
            await session.commit()


class RunManager:
    """运行实例注册表：事件总线 + 后台任务生命周期。"""

    def __init__(self) -> None:
        self._buses: dict[uuid.UUID, EventBus] = {}
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}

    def bus_for(self, run_id: uuid.UUID) -> EventBus:
        if run_id not in self._buses:
            self._buses[run_id] = EventBus(run_id)
        return self._buses[run_id]

    def existing_bus(self, run_id: uuid.UUID) -> EventBus | None:
        return self._buses.get(run_id)

    def start(self, run_id: uuid.UUID, coro: Callable[[], Awaitable[None]]) -> None:
        task = asyncio.create_task(self._run_and_cleanup(run_id, coro), name=f"run-{run_id}")
        self._tasks[run_id] = task

    async def _run_and_cleanup(
        self, run_id: uuid.UUID, coro: Callable[[], Awaitable[None]]
    ) -> None:
        try:
            await coro()
        except asyncio.CancelledError:
            pass  # execute_run 内部已处理状态
        finally:
            bus = self._buses.pop(run_id, None)
            if bus is not None:
                await bus.close()
            self._tasks.pop(run_id, None)

    def cancel(self, run_id: uuid.UUID) -> bool:
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            return True
        return False

    async def replay(self, run_id: uuid.UUID, after_seq: int) -> list[Event]:
        """从 events 表回放（SSE 断线补拉 / Timeline 数据源）。"""
        async with SessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(EventRow)
                        .where(EventRow.run_id == run_id, EventRow.seq > after_seq)
                        .order_by(EventRow.seq)
                    )
                )
                .scalars()
                .all()
            )
        return [
            Event(
                seq=r.seq,
                run_id=str(r.run_id),
                type=r.type,
                payload=r.payload or {},
                ts=r.ts.isoformat(),
            )
            for r in rows
        ]


manager = RunManager()

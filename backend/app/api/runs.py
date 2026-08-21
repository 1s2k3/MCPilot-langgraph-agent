"""运行（Run）API：创建 / 摘要 / SSE 流 / 事件回放 / 取消 / HITL 决议。"""

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agent.runner import execute_run, manager
from app.api.deps import get_session
from app.api.schemas import RunCreate, RunOut
from app.core.errors import AppError, not_found
from app.core.logging import get_logger
from app.db.models import Agent, Message, Run, Thread
from app.db.session import SessionLocal
from app.events.models import TERMINAL_EVENT_TYPES

router = APIRouter(prefix="/threads", tags=["runs"])
logger = get_logger(__name__)

_ACTIVE_RUN_STATUSES = ("pending", "running", "interrupted")

# run 级别名路由（RunDetail 页 / 记忆溯源跳转用，无需 thread_id）
alias_router = APIRouter(prefix="/runs", tags=["runs"])


@alias_router.get("/{run_id}", response_model=RunOut)
async def get_run_alias(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> Run:
    run = await session.get(Run, run_id)
    if run is None:
        raise not_found("Run", str(run_id))
    return run


@alias_router.get("/{run_id}/events")
async def list_events_alias(
    run_id: uuid.UUID,
    after_seq: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await session.get(Run, run_id)
    if run is None:
        raise not_found("Run", str(run_id))
    events = await manager.replay(run_id, after_seq)
    return {"events": [e.model_dump(mode="json") for e in events]}


@alias_router.get("/{run_id}/tool-calls")
async def list_tool_calls_alias(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    """运行的工具调用记录（含结果，工具可视化数据源）。"""
    from app.db.models import ToolCall

    run = await session.get(Run, run_id)
    if run is None:
        raise not_found("Run", str(run_id))
    rows = (
        await session.execute(
            select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.created_at)
        )
    ).scalars()
    return {
        "tool_calls": [
            {
                "id": str(r.id),
                "tool_name": r.tool_name,
                "server": r.server,
                "args": r.args,
                "result": r.result,
                "status": r.status,
                "duration_ms": r.duration_ms,
                "error": r.error,
                "truncated": r.truncated,
            }
            for r in rows
        ]
    }


async def _resolve_agent(session: AsyncSession, thread: Thread, body: RunCreate) -> Agent:
    agent_id = body.agent_id or thread.agent_id
    if agent_id is not None:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise not_found("Agent", str(agent_id))
        return agent
    agent = (
        await session.execute(
            select(Agent).where(Agent.enabled).order_by(Agent.created_at).limit(1)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise not_found("Agent", "默认 Agent 不存在，请先创建")
    return agent


@router.post("/{thread_id}/runs", status_code=status.HTTP_201_CREATED)
async def create_run(
    thread_id: uuid.UUID, body: RunCreate, session: AsyncSession = Depends(get_session)
) -> dict:
    """启动运行：落库 run + 用户消息，注册后台任务，返回 run_id（执行与流式均异步）。"""
    thread = await session.get(Thread, thread_id)
    if thread is None:
        raise not_found("Thread", str(thread_id))
    # v1：同一线程同时只允许一个活动 run（checkpoint 一致性前提）
    active = (
        (
            await session.execute(
                select(Run).where(Run.thread_id == thread_id, Run.status.in_(_ACTIVE_RUN_STATUSES))
            )
        )
        .scalars()
        .first()
    )
    if active is not None:
        raise AppError(
            "run_in_progress", f"线程 {thread_id} 已有活动运行 {active.id}", status_code=409
        )

    agent = await _resolve_agent(session, thread, body)
    run = Run(
        id=uuid.uuid4(), thread_id=thread_id, agent_id=agent.id, status="pending", input=body.input
    )
    session.add(run)
    seq = (
        await session.execute(
            select(func.coalesce(func.max(Message.seq), 0)).where(Message.thread_id == thread_id)
        )
    ).scalar_one() + 1
    session.add(
        Message(thread_id=thread_id, run_id=run.id, role="user", content=body.input, seq=seq)
    )
    if not thread.title:
        thread.title = body.input[:50]
    await session.commit()

    bus = manager.bus_for(run.id)
    manager.start(run.id, lambda: execute_run(run.id, thread.id, agent, body.input, bus))
    logger.info("run_created", run_id=str(run.id), thread_id=str(thread_id))
    return {"run_id": str(run.id)}


@router.get("/{thread_id}/runs", response_model=list[RunOut])
async def list_runs(
    thread_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[Run]:
    """线程 run 列表（前端恢复活动 run / 历史记录）。"""
    thread = await session.get(Thread, thread_id)
    if thread is None:
        raise not_found("Thread", str(thread_id))
    return list(
        (
            await session.execute(
                select(Run)
                .where(Run.thread_id == thread_id)
                .order_by(Run.created_at.desc())
                .limit(50)
            )
        ).scalars()
    )


@router.get("/{thread_id}/runs/{run_id}", response_model=RunOut)
async def get_run(
    thread_id: uuid.UUID, run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Run:
    run = await session.get(Run, run_id)
    if run is None or run.thread_id != thread_id:
        raise not_found("Run", str(run_id))
    return run


@router.get("/{thread_id}/runs/{run_id}/events")
async def list_events(
    thread_id: uuid.UUID,
    run_id: uuid.UUID,
    after_seq: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """事件回放（Timeline 数据源），after_seq 支持增量补拉。"""
    run = await session.get(Run, run_id)
    if run is None or run.thread_id != thread_id:
        raise not_found("Run", str(run_id))
    events = await manager.replay(run_id, after_seq)
    return {"events": [e.model_dump(mode="json") for e in events]}


@router.get("/{thread_id}/runs/{run_id}/stream")
async def run_stream(
    thread_id: uuid.UUID, run_id: uuid.UUID, request: Request
) -> EventSourceResponse:
    """SSE 事件流：Last-Event-ID(=seq) 幂等补拉 + 实时推送；run_end 后关闭。"""
    async with SessionLocal() as session:
        run = await session.get(Run, run_id)
        if run is None or run.thread_id != thread_id:
            raise not_found("Run", str(run_id))

    last_event_id = request.headers.get("last-event-id")

    async def _stream():
        last_seq = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
        seen: set[int] = set()
        bus = manager.existing_bus(run_id)
        # run 已结束（bus 已清理，事件已全部落库）→ 纯回放
        if bus is None or bus.closed:
            for evt in await manager.replay(run_id, last_seq):
                if evt.seq <= last_seq:
                    continue
                yield _sse_item(evt)
            return
        q = bus.subscribe()
        try:
            # 先订阅再回放：回放期间新发布的事件不会漏（进队列），
            # 与回放重复的按 seq 去重 → 客户端收到的始终按 seq 升序
            for evt in await manager.replay(run_id, last_seq):
                if evt.seq <= last_seq:
                    continue
                seen.add(evt.seq)
                yield _sse_item(evt)
            while True:
                evt = await q.get()
                if evt.seq in seen:
                    continue
                seen.add(evt.seq)
                yield _sse_item(evt)
                if evt.type in TERMINAL_EVENT_TYPES:
                    return
        finally:
            bus.unsubscribe(q)

    return EventSourceResponse(
        _stream(),
        ping=15,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_item(evt) -> dict:
    return {"event": "message", "id": str(evt.seq), "data": json.dumps(evt.model_dump(mode="json"))}


@router.post("/{thread_id}/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(
    thread_id: uuid.UUID, run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    run = await session.get(Run, run_id)
    if run is None or run.thread_id != thread_id:
        raise not_found("Run", str(run_id))
    if not manager.cancel(run_id):
        raise AppError("cancel_failed", "运行已结束或不存在于当前进程", status_code=409)
    return {"cancelled": True}


class RunResumeBody(BaseModel):
    """HITL 决议（§5.9）：对 interrupt 挂起的工具审批。"""

    action: Literal["approve", "deny"]
    feedback: str = ""
    session_wide: bool = False  # 本次会话内不再询问该工具


@router.post("/{thread_id}/runs/{run_id}/resume")
async def resume_run(
    thread_id: uuid.UUID,
    run_id: uuid.UUID,
    body: RunResumeBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """提交人工决议，唤醒挂起的运行（LangGraph Command(resume=...)）。"""
    run = await session.get(Run, run_id)
    if run is None or run.thread_id != thread_id:
        raise not_found("Run", str(run_id))
    if run.status != "interrupted":
        raise AppError("not_interrupted", "运行未处于等待审批状态", status_code=409)
    decision = {"action": body.action, "feedback": body.feedback, "session_wide": body.session_wide}
    if not manager.resume(run_id, decision):
        raise AppError("resume_failed", "无法恢复（当前进程内没有等待中的审批）", status_code=409)
    return {"resumed": True}

"""会话（Thread）API。"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.api.schemas import MessageOut, ThreadCreate, ThreadOut
from app.core.errors import not_found
from app.db.models import Message, Thread

router = APIRouter(prefix="/threads", tags=["threads"])


@router.post("", response_model=ThreadOut, status_code=status.HTTP_201_CREATED)
async def create_thread(body: ThreadCreate, session: AsyncSession = Depends(get_session)) -> Thread:
    thread = Thread(agent_id=body.agent_id, title=body.title)
    session.add(thread)
    await session.commit()
    await session.refresh(thread)
    return thread


@router.get("", response_model=list[ThreadOut])
async def list_threads(session: AsyncSession = Depends(get_session)) -> list[Thread]:
    return list(
        (await session.execute(select(Thread).order_by(Thread.updated_at.desc()))).scalars()
    )


@router.get("/{thread_id}", response_model=ThreadOut)
async def get_thread(thread_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> Thread:
    thread = await session.get(Thread, thread_id)
    if thread is None:
        raise not_found("Thread", str(thread_id))
    return thread


@router.get("/{thread_id}/messages", response_model=list[MessageOut])
async def list_messages(
    thread_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[Message]:
    thread = await session.get(Thread, thread_id)
    if thread is None:
        raise not_found("Thread", str(thread_id))
    return list(
        (
            await session.execute(
                select(Message).where(Message.thread_id == thread_id).order_by(Message.seq)
            )
        ).scalars()
    )

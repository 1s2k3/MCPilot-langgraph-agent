"""Agent 配置 CRUD。"""

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.api.schemas import AgentCreate, AgentOut, AgentUpdate
from app.core.errors import not_found
from app.db.models import Agent

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
async def list_agents(session: AsyncSession = Depends(get_session)) -> list[Agent]:
    return list((await session.execute(select(Agent).order_by(Agent.created_at))).scalars())


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(body: AgentCreate, session: AsyncSession = Depends(get_session)) -> Agent:
    agent = Agent(**body.model_dump())
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


@router.get("/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise not_found("Agent", str(agent_id))
    return agent


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: uuid.UUID, body: AgentUpdate, session: AsyncSession = Depends(get_session)
) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise not_found("Agent", str(agent_id))
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(agent, key, value)
    await session.commit()
    await session.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise not_found("Agent", str(agent_id))
    await session.delete(agent)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

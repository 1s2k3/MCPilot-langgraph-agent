"""评估 API（§11.3）：数据集 CRUD + 评估运行 + 指标/评分查询。"""

import asyncio
import uuid

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import AppError, not_found
from app.core.logging import get_logger
from app.db.models import Agent, EvalDataset, EvalRun, EvalScore
from app.eval.runner import run_eval

router = APIRouter(prefix="/eval", tags=["eval"])
logger = get_logger(__name__)


class EvalEntry(BaseModel):
    input: str = Field(min_length=1)
    expected_tool_calls: list[str] = Field(default_factory=list)
    reference_answer: str | None = None
    rubric: str | None = None
    category: str = ""
    difficulty: int = Field(default=1, ge=1, le=3)


class EvalDatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    entries: list[EvalEntry] = Field(min_length=1)


class EvalDatasetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    entries: list[EvalEntry] | None = None


class EvalRunStart(BaseModel):
    agent_id: uuid.UUID | None = None


def _dataset_out(row: EvalDataset) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "entries": row.entries or [],
        "entry_count": len(row.entries or []),
        "created_at": row.created_at.isoformat(),
    }


def _run_out(row: EvalRun) -> dict:
    return {
        "id": str(row.id),
        "dataset_id": str(row.dataset_id),
        "agent_id": str(row.agent_id) if row.agent_id else None,
        "status": row.status,
        "model_snapshot": row.model_snapshot or {},
        "metrics": row.metrics or {},
        "created_at": row.created_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


@router.get("/datasets")
async def list_datasets(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (
        await session.execute(select(EvalDataset).order_by(EvalDataset.created_at.desc()))
    ).scalars()
    return {"datasets": [_dataset_out(r) for r in rows]}


@router.post("/datasets", status_code=status.HTTP_201_CREATED)
async def create_dataset(
    body: EvalDatasetCreate, session: AsyncSession = Depends(get_session)
) -> dict:
    dup = (
        await session.execute(select(EvalDataset).where(EvalDataset.name == body.name))
    ).scalar_one_or_none()
    if dup is not None:
        raise AppError("already_exists", f"数据集 {body.name} 已存在", status_code=409)
    row = EvalDataset(
        name=body.name,
        description=body.description,
        entries=[e.model_dump() for e in body.entries],
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _dataset_out(row)


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    row = await session.get(EvalDataset, dataset_id)
    if row is None:
        raise not_found("Dataset", str(dataset_id))
    return _dataset_out(row)


@router.patch("/datasets/{dataset_id}")
async def update_dataset(
    dataset_id: uuid.UUID, body: EvalDatasetUpdate, session: AsyncSession = Depends(get_session)
) -> dict:
    row = await session.get(EvalDataset, dataset_id)
    if row is None:
        raise not_found("Dataset", str(dataset_id))
    updates = body.model_dump(exclude_unset=True)
    if "entries" in updates:
        updates["entries"] = [e.model_dump() for e in body.entries]  # type: ignore[union-attr]
    for key, value in updates.items():
        setattr(row, key, value)
    await session.commit()
    await session.refresh(row)
    return _dataset_out(row)


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    dataset_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    row = await session.get(EvalDataset, dataset_id)
    if row is None:
        raise not_found("Dataset", str(dataset_id))
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/datasets/{dataset_id}/runs", status_code=status.HTTP_201_CREATED)
async def start_eval_run(
    dataset_id: uuid.UUID,
    body: EvalRunStart,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """启动评估运行（后台任务）。"""
    dataset = await session.get(EvalDataset, dataset_id)
    if dataset is None:
        raise not_found("Dataset", str(dataset_id))
    agent_id = body.agent_id
    if agent_id is None:
        agent = (
            await session.execute(
                select(Agent).where(Agent.enabled).order_by(Agent.created_at).limit(1)
            )
        ).scalar_one_or_none()
        agent_id = agent.id if agent else None
    if agent_id is None:
        raise not_found("Agent", "默认 Agent 不存在，请先创建")
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise not_found("Agent", str(agent_id))
    erun = EvalRun(dataset_id=dataset_id, agent_id=agent_id, status="pending")
    session.add(erun)
    await session.commit()
    await session.refresh(erun)
    asyncio.create_task(run_eval(erun.id), name=f"eval-{erun.id}")
    logger.info("eval_started", eval_run_id=str(erun.id), dataset=dataset.name)
    return {"eval_run_id": str(erun.id)}


@router.get("/runs")
async def list_eval_runs(session: AsyncSession = Depends(get_session)) -> dict:
    rows = (
        await session.execute(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(50))
    ).scalars()
    return {"runs": [_run_out(r) for r in rows]}


@router.get("/runs/{eval_run_id}")
async def get_eval_run(
    eval_run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    row = await session.get(EvalRun, eval_run_id)
    if row is None:
        raise not_found("EvalRun", str(eval_run_id))
    return _run_out(row)


@router.get("/runs/{eval_run_id}/scores")
async def list_eval_scores(
    eval_run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    row = await session.get(EvalRun, eval_run_id)
    if row is None:
        raise not_found("EvalRun", str(eval_run_id))
    rows = (
        await session.execute(
            select(EvalScore)
            .where(EvalScore.eval_run_id == eval_run_id)
            .order_by(EvalScore.entry_index)
        )
    ).scalars()
    return {
        "scores": [
            {
                "entry_index": r.entry_index,
                "input": r.input,
                "trajectory": r.trajectory or {},
                "tool_seq_match": r.tool_seq_match,
                "answer_score": r.answer_score,
                "judge_reason": r.judge_reason,
                "error": r.error,
            }
            for r in rows
        ]
    }

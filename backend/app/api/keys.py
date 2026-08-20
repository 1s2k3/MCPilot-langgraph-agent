"""API Key 管理（§9）：加密落库、写后不可读、只返回掩码。"""

import uuid

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import not_found
from app.db.models import ApiKey
from app.security.keys import encrypt_secret

router = APIRouter(prefix="/api-keys", tags=["keys"])


class ApiKeyCreate(BaseModel):
    provider: str = Field(default="anthropic", pattern=r"^[a-z0-9_-]+$", max_length=50)
    name: str = Field(min_length=1, max_length=200)
    key: str = Field(min_length=8, max_length=1000)


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    provider: str
    name: str
    masked: str
    last_used_at: object | None
    created_at: object


def _out(row: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=row.id,
        provider=row.provider,
        name=row.name,
        masked="***",  # 写后不可读：连掩码都不回显原值（安全起见）
        last_used_at=row.last_used_at,
        created_at=row.created_at,
    )


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(session: AsyncSession = Depends(get_session)) -> list[ApiKeyOut]:
    rows = list((await session.execute(select(ApiKey).order_by(ApiKey.created_at))).scalars())
    return [_out(r) for r in rows]


@router.post("", response_model=ApiKeyOut, status_code=status.HTTP_201_CREATED)
async def create_key(body: ApiKeyCreate, session: AsyncSession = Depends(get_session)) -> ApiKeyOut:
    row = ApiKey(
        provider=body.provider,
        name=body.name,
        key_ciphertext=encrypt_secret(body.key),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _out(row)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_key(key_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> Response:
    row = await session.get(ApiKey, key_id)
    if row is None:
        raise not_found("API Key", str(key_id))
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

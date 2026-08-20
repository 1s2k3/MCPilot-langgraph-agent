"""MCP Server 管理 API：CRUD + 连接测试（凭据加密落库，只读掩码）。"""

import json
import uuid

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.errors import AppError, not_found
from app.core.logging import get_logger
from app.db.models import McpServer
from app.security.keys import decrypt_secret, encrypt_secret, mask_key
from app.tools.mcp_client import mcp_manager

router = APIRouter(prefix="/mcp-servers", tags=["mcp"])
logger = get_logger(__name__)

_TRANSPORTS = ("stdio", "streamable_http")


class McpServerCreate(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=200)
    transport: str = Field(pattern=r"^(stdio|streamable_http)$")
    command: str | None = None  # stdio 用
    args: list[str] = Field(default_factory=list)
    url: str | None = None  # streamable_http 用
    headers: dict[str, str] | None = None  # 明文入参 → 加密落库
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    tool_allowlist: list[str] | None = None  # None = 全部允许


class McpServerUpdate(BaseModel):
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    env: dict[str, str] | None = None
    enabled: bool | None = None
    tool_allowlist: list[str] | None = None


class McpServerOut(BaseModel):
    id: uuid.UUID
    name: str
    transport: str
    command: str | None
    args: list | None
    url: str | None
    env: dict | None
    enabled: bool
    tool_allowlist: list | None
    headers_masked: dict[str, str] = Field(default_factory=dict)
    health: str = "unknown"
    created_at: object
    updated_at: object


def _validate(body: McpServerCreate) -> None:
    if body.transport == "stdio" and not body.command:
        raise AppError("validation_error", "stdio 传输需要 command")
    if body.transport == "streamable_http" and not body.url:
        raise AppError("validation_error", "streamable_http 传输需要 url")


def _out(row: McpServer) -> McpServerOut:
    masked: dict[str, str] = {}
    if row.headers_encrypted:
        try:
            headers = json.loads(decrypt_secret(row.headers_encrypted))
            masked = {k: mask_key(v) for k, v in headers.items()}
        except AppError:
            masked = {"<encrypted>": "***"}
    return McpServerOut(
        id=row.id,
        name=row.name,
        transport=row.transport,
        command=row.command,
        args=row.args,
        url=row.url,
        env=row.env,
        enabled=row.enabled,
        tool_allowlist=row.tool_allowlist,
        headers_masked=masked,
        health=mcp_manager.health().get(row.name, "unknown"),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _apply_headers(row: McpServer, headers: dict[str, str] | None) -> None:
    """headers: None → 不改；dict（可为空）→ 加密覆盖。"""
    if headers is None:
        return
    row.headers_encrypted = encrypt_secret(json.dumps(headers, ensure_ascii=False))


@router.get("", response_model=list[McpServerOut])
async def list_servers(session: AsyncSession = Depends(get_session)) -> list[McpServerOut]:
    rows = list((await session.execute(select(McpServer).order_by(McpServer.created_at))).scalars())
    return [_out(r) for r in rows]


@router.post("", response_model=McpServerOut, status_code=status.HTTP_201_CREATED)
async def create_server(
    body: McpServerCreate, session: AsyncSession = Depends(get_session)
) -> McpServerOut:
    _validate(body)
    dup = (
        await session.execute(select(McpServer).where(McpServer.name == body.name))
    ).scalar_one_or_none()
    if dup is not None:
        raise AppError("already_exists", f"MCP server {body.name} 已存在", status_code=409)
    row = McpServer(
        name=body.name,
        transport=body.transport,
        command=body.command,
        args=body.args,
        url=body.url,
        env=body.env,
        enabled=body.enabled,
        tool_allowlist=body.tool_allowlist,
    )
    _apply_headers(row, body.headers)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await mcp_manager.refresh()
    return _out(row)


@router.get("/{server_id}", response_model=McpServerOut)
async def get_server(
    server_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> McpServerOut:
    row = await session.get(McpServer, server_id)
    if row is None:
        raise not_found("MCP server", str(server_id))
    return _out(row)


@router.patch("/{server_id}", response_model=McpServerOut)
async def update_server(
    server_id: uuid.UUID, body: McpServerUpdate, session: AsyncSession = Depends(get_session)
) -> McpServerOut:
    row = await session.get(McpServer, server_id)
    if row is None:
        raise not_found("MCP server", str(server_id))
    updates = body.model_dump(exclude_unset=True)
    headers = updates.pop("headers", None)
    for key, value in updates.items():
        setattr(row, key, value)
    if row.transport == "stdio" and not row.command:
        raise AppError("validation_error", "stdio 传输需要 command")
    if row.transport == "streamable_http" and not row.url:
        raise AppError("validation_error", "streamable_http 传输需要 url")
    _apply_headers(row, headers)
    await session.commit()
    await session.refresh(row)
    await mcp_manager.refresh()
    return _out(row)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    row = await session.get(McpServer, server_id)
    if row is None:
        raise not_found("MCP server", str(server_id))
    await session.delete(row)
    await session.commit()
    await mcp_manager.refresh()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{server_id}/test")
async def test_server(server_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    """连接测试：列该 server 当前导出的工具。"""
    row = await session.get(McpServer, server_id)
    if row is None:
        raise not_found("MCP server", str(server_id))
    try:
        tools = await mcp_manager.test_server(row)
        return {"ok": True, "server": row.name, "tools": tools}
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp_test_failed", server=row.name, error=str(exc)[:200])
        return {"ok": False, "server": row.name, "error": str(exc)[:500]}

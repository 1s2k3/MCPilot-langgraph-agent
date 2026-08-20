"""MCP Server 管理 API：CRUD + 连接测试（凭据加密落库，只读掩码）。

安全（§9 / 安全评审加固）：
- stdio command 走可执行白名单（settings.mcp_command_allowlist）
- streamable_http url 仅 http/https，且默认拒绝回环/私有网段（SSRF 基础防护）
- headers 与 env 同等加密落库，响应只返回掩码
"""

import ipaddress
import json
import socket
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import get_settings
from app.core.errors import AppError, not_found
from app.core.logging import get_logger
from app.db.models import McpServer
from app.security.keys import decrypt_secret, encrypt_secret
from app.tools.mcp_client import mcp_manager

router = APIRouter(prefix="/mcp-servers", tags=["mcp"])
logger = get_logger(__name__)

_PRIVATE_HOSTNAMES = ("localhost", "metadata.google.internal", "metadata")


class McpServerCreate(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]+$", max_length=200)
    transport: str = Field(pattern=r"^(stdio|streamable_http)$")
    command: str | None = None  # stdio 用
    args: list[str] = Field(default_factory=list)
    url: str | None = None  # streamable_http 用
    headers: dict[str, str] | None = None  # 明文入参 → 加密落库
    env: dict[str, str] = Field(default_factory=dict)  # 明文入参 → 加密落库
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
    env_masked: dict[str, str] = Field(default_factory=dict)
    enabled: bool
    tool_allowlist: list | None
    headers_masked: dict[str, str] = Field(default_factory=dict)
    health: str = "unknown"
    created_at: object
    updated_at: object


def _validate_command(command: str) -> None:
    allowlist = [c.strip() for c in get_settings().mcp_command_allowlist.split(",") if c.strip()]
    base = command.split()[0]
    if base not in allowlist:
        raise AppError(
            "validation_error",
            f"command 不在允许列表（{', '.join(allowlist)}），可经 MCP_COMMAND_ALLOWLIST 配置",
        )


def _validate_url(url: str) -> None:
    """SSRF 基础防护：仅 http(s)；默认拒绝回环/私有/链路本地目标。"""
    if not url.startswith(("http://", "https://")):
        raise AppError("validation_error", "url 仅支持 http:// 或 https://")
    host = urlparse(url).hostname or ""
    if not host:
        raise AppError("validation_error", "url 无法解析主机名")
    if get_settings().allow_private_mcp_urls:
        return  # 本地开发/集成测试豁免（生产保持关闭）
    if host.lower() in _PRIVATE_HOSTNAMES or host.endswith(".local"):
        raise AppError("validation_error", f"url 目标 {host} 不允许（私有/回环地址）")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise AppError("validation_error", f"url 主机名无法解析: {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise AppError("validation_error", f"url 目标解析到私有/保留地址 {ip}，已拒绝")


def _validate(body: McpServerCreate) -> None:
    if body.transport == "stdio":
        if not body.command:
            raise AppError("validation_error", "stdio 传输需要 command")
        _validate_command(body.command)
    if body.transport == "streamable_http":
        if not body.url:
            raise AppError("validation_error", "streamable_http 传输需要 url")
        _validate_url(body.url)


def _out(row: McpServer) -> McpServerOut:
    masked: dict[str, str] = {}
    if row.headers_encrypted:
        try:
            headers = json.loads(decrypt_secret(row.headers_encrypted))
            masked = {k: "***" for k in headers}
        except AppError:
            masked = {"<encrypted>": "***"}
    env_masked: dict[str, str] = {}
    if row.env_encrypted:
        env_masked = {"<encrypted>": "***"}
    return McpServerOut(
        id=row.id,
        name=row.name,
        transport=row.transport,
        command=row.command,
        args=row.args,
        url=row.url,
        env_masked=env_masked,
        enabled=row.enabled,
        tool_allowlist=row.tool_allowlist,
        headers_masked=masked,
        health=mcp_manager.health().get(row.name, "unknown"),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _apply_secrets(
    row: McpServer, headers: dict[str, str] | None, env: dict[str, str] | None
) -> None:
    """headers/env: None → 不改；dict（可为空）→ 加密覆盖。"""
    if headers is not None:
        row.headers_encrypted = encrypt_secret(json.dumps(headers, ensure_ascii=False))
    if env is not None:
        row.env_encrypted = encrypt_secret(json.dumps(env, ensure_ascii=False))


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
        enabled=body.enabled,
        tool_allowlist=body.tool_allowlist,
    )
    _apply_secrets(row, body.headers, body.env)
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
    env = updates.pop("env", None)
    for key, value in updates.items():
        setattr(row, key, value)
    if row.transport == "stdio":
        if not row.command:
            raise AppError("validation_error", "stdio 传输需要 command")
        _validate_command(row.command)
    if row.transport == "streamable_http":
        if not row.url:
            raise AppError("validation_error", "streamable_http 传输需要 url")
        _validate_url(row.url)
    _apply_secrets(row, headers, env)
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
        return {"ok": False, "server": row.name, "error": str(exc)[:300]}

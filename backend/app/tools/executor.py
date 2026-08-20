"""工具执行器（§5.8 结果管理）：事件、落库、规范化、截断、脱敏。

安全约定：args/result 落库与回喂前一律脱敏（敏感键名打码），超限截断防上下文膨胀。
"""

import asyncio
import json
import time
import uuid

from langchain_core.messages import ToolMessage

from app.core.config import get_settings
from app.db.models import ToolCall as ToolCallRow
from app.events.bus import EventBus
from app.tools.registry import ToolMeta

_SENSITIVE_KEY_PARTS = ("key", "token", "secret", "password", "authorization", "credential")


def mask_secrets(obj, depth: int = 0):
    """递归脱敏：键名含敏感词 → 值替换为 ***。"""
    if depth > 8 or obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {
            str(k): (
                "***"
                if any(p in str(k).lower() for p in _SENSITIVE_KEY_PARTS)
                else mask_secrets(v, depth + 1)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [mask_secrets(v, depth + 1) for v in obj]
    return str(obj)


def to_json_safe(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json_safe(v) for v in obj]
    if hasattr(obj, "model_dump"):
        return to_json_safe(obj.model_dump())
    return str(obj)


def _extract_text_blocks(blocks) -> list[str]:
    """MCP 风格 content blocks → 文本片段。"""
    return [
        b.get("text", "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
    ]


def normalize_result(result) -> dict:
    """统一为 {ok, data} 结构。"""
    if isinstance(result, str):
        return {"ok": True, "data": result}
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        texts = _extract_text_blocks(result["content"])
        return {"ok": True, "data": "\n".join(texts) if texts else to_json_safe(result["content"])}
    if isinstance(result, list):
        texts = _extract_text_blocks(result)
        return {"ok": True, "data": "\n".join(texts) if texts else to_json_safe(result)}
    return {"ok": True, "data": to_json_safe(result)}


def store_result(normalized: dict, cap_bytes: int) -> tuple[dict, bool]:
    """落库版本：脱敏 + 超限截断（返回 (payload, truncated)）。"""
    payload = mask_secrets(normalized)
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw.encode("utf-8")) <= cap_bytes:
        return payload, False
    return {"ok": normalized.get("ok"), "truncated": True, "preview": raw[:cap_bytes]}, True


def feedback_text(normalized: dict, cap_chars: int) -> str:
    """回喂 LLM 的文本（截断预算，防上下文膨胀）。"""
    data = normalized.get("data")
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) > cap_chars:
        text = text[:cap_chars] + f"\n…（输出过长已截断，共 {len(text)} 字符）"
    return text


async def execute_tool_call(
    meta: ToolMeta,
    tool_call: dict,
    *,
    bus: EventBus,
    run_id: uuid.UUID,
    session,
) -> ToolMessage:
    """执行一次工具调用：事件 + 落库 + 规范化；异常包装为 is_error ToolMessage 回喂 LLM。"""
    settings = get_settings()
    name = tool_call.get("name", "")
    args = tool_call.get("args") or {}
    call_id = tool_call.get("id") or f"call-{uuid.uuid4()}"
    masked_args = to_json_safe(mask_secrets(args))

    row = ToolCallRow(
        id=uuid.uuid4(),
        run_id=run_id,
        tool_name=name,
        server=meta.server,
        args=masked_args,
        status="running",
    )
    if session is not None:  # 无 DB 会话时（图级测试）跳过落库，执行与事件照常
        session.add(row)
        await session.commit()
    await bus.publish(
        bus.next_event(
            "tool_call_start",
            {"id": str(row.id), "name": name, "server": meta.server, "args": masked_args},
        )
    )

    start = time.perf_counter()
    try:
        async with asyncio.timeout(settings.tool_timeout_seconds):
            raw = await meta.tool.ainvoke(args)
        normalized = normalize_result(raw)
        payload, truncated = store_result(normalized, settings.tool_result_cap_bytes)
        row.result = payload
        row.status = "succeeded"
        row.truncated = truncated
        await bus.publish(
            bus.next_event(
                "tool_call_end",
                {
                    "id": str(row.id),
                    "name": name,
                    "status": row.status,
                    "duration_ms": int((time.perf_counter() - start) * 1000),
                    "truncated": truncated,
                },
            )
        )
        return ToolMessage(
            content=feedback_text(normalized, settings.tool_feedback_cap_chars),
            tool_call_id=call_id,
            status="success",
        )
    except TimeoutError:
        row.status = "failed"
        row.error = "工具执行超时"
        await bus.publish(
            bus.next_event(
                "tool_call_end",
                {"id": str(row.id), "name": name, "status": "failed", "error": row.error},
            )
        )
        return ToolMessage(content=f"工具 {name} 执行超时", tool_call_id=call_id, status="error")
    except Exception as exc:  # noqa: BLE001
        row.status = "failed"
        row.error = str(exc)[:1000]
        await bus.publish(
            bus.next_event(
                "tool_call_end",
                {"id": str(row.id), "name": name, "status": "failed", "error": row.error},
            )
        )
        return ToolMessage(
            content=f"工具 {name} 执行失败: {row.error}", tool_call_id=call_id, status="error"
        )
    finally:
        row.duration_ms = int((time.perf_counter() - start) * 1000)
        if session is not None:
            await session.commit()

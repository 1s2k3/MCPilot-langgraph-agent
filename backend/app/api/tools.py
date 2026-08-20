"""工具清单 API：本地 + MCP 聚合视图（前端工具面板 / 权限矩阵数据源）。"""

from fastapi import APIRouter

from app.tools.mcp_client import mcp_manager
from app.tools.registry import build_registry

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("")
async def list_tools() -> dict:
    metas = list(build_registry().all_metas()) + list(mcp_manager.tools().values())
    items = [
        {
            "name": m.tool.name,
            "server": m.server,
            "source": m.source,
            "render_hint": m.render_hint,
            "policy": m.policy,
            "description": m.tool.description or "",
        }
        for m in metas
    ]
    items.sort(key=lambda x: (x["server"], x["name"]))
    return {"tools": items}

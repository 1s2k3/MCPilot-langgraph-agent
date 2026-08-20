"""工具注册表：本地工具与 MCP 工具的统一入口。"""

import uuid
from dataclasses import dataclass

from langchain_core.tools import BaseTool

from app.tools.local import calculator, get_current_time
from app.tools.memory_tools import build_memory_tools


@dataclass
class ToolMeta:
    tool: BaseTool
    server: str = "local"  # local | <mcp server 名>
    render_hint: str = "text"  # text | json | markdown | table
    policy: str = "allow"  # allow | ask | deny（M5 完整接入）
    source: str = "local"  # local | mcp


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolMeta] = {}

    def register(self, name: str, meta: ToolMeta) -> None:
        self._tools[name] = meta

    def get(self, name: str) -> ToolMeta | None:
        return self._tools.get(name)

    def all_metas(self) -> list[ToolMeta]:
        return list(self._tools.values())

    def langchain_tools(self, names: set[str] | None = None) -> list[BaseTool]:
        """LLM 绑定用工具列表；names 为空集时不绑定任何工具。"""
        return [m.tool for n, m in self._tools.items() if names is None or n in names]


def build_registry(thread_id: uuid.UUID | None = None) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(calculator.name, ToolMeta(tool=calculator))
    reg.register(get_current_time.name, ToolMeta(tool=get_current_time))
    remember, forget = build_memory_tools(thread_id)
    reg.register(remember.name, ToolMeta(tool=remember))
    reg.register(forget.name, ToolMeta(tool=forget))
    return reg

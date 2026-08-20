"""工具注册表：本地工具与 MCP 工具（M2）的统一入口。"""

from dataclasses import dataclass

from langchain_core.tools import BaseTool

from app.tools.local import calculator, get_current_time


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


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(calculator.name, ToolMeta(tool=calculator))
    reg.register(get_current_time.name, ToolMeta(tool=get_current_time))
    return reg

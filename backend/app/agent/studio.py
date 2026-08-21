"""LangGraph Studio 本地调试入口（`langgraph dev` / smith.langchain.com/studio）。

独立于生产 FastAPI 路径：Studio 进程内构建最小可用 GraphContext——
真实 LLM（env 驱动）+ 内置工具 + MCP best-effort（DB 可达时加载），
工具策略默认 allow（调试过程不触发 HITL 阻塞）。

启动（backend 目录）:
    .venv/Scripts/langgraph dev --host 127.0.0.1 --port 2024
浏览器打开: https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
注：langgraph-cli 新版默认内存 store，已无 --no-docker 选项。
"""

import os
import uuid

from langchain_core.messages import convert_to_messages

from app.agent.graph import GraphContext, build_graph
from app.agent.runner import build_node_llms
from app.core.logging import get_logger
from app.db.models import Agent
from app.tools.mcp_client import mcp_manager
from app.tools.registry import build_registry

logger = get_logger(__name__)

# Studio 本地进程连容器内 mcp-demo 用（compose 已映射 8001 到宿主机）
os.environ.setdefault("MCP_DEMO_URL", "http://localhost:8001/mcp")

_SYSTEM_PROMPT = (
    "你是一个通用助手。基于工具结果回答用户问题；没有可用工具时如实说明能力边界，不要臆造结果。"
)


async def build_studio_graph():
    """Studio 图工厂（async）：构建真实 LLM + 内置/MCP 工具的图。

    langgraph-cli 的 graphs 入口支持 async 工厂函数。
    """
    llms, structured_fn = await build_node_llms(Agent(name="studio-debug"))
    registry = build_registry(uuid.uuid4())
    try:
        await mcp_manager.refresh()  # best-effort：加载已注册的 MCP server 工具
        for name, meta in mcp_manager.tools().items():
            registry.register(name, meta)
    except Exception:  # noqa: BLE001
        logger.warning("studio_mcp_unavailable", exc_info=True)
    ctx = GraphContext(
        llms=llms,
        registry=registry,
        system_prompt=_SYSTEM_PROMPT,
        tool_policy={"default": "allow"},  # 调试免 HITL
        structured_fn=structured_fn,
    )
    return _coerce_inputs(build_graph(ctx))


def _coerce_inputs(compiled):
    """LangGraph Server 直接把 OpenAI 格式的 dict 消息传给图（不自动转换），
    这里包装入口把 dict 消息转成 langchain BaseMessage（图内统一按对象处理）。"""

    def _convert(input):
        if isinstance(input, dict):
            msgs = input.get("messages")
            if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict):
                input = {**input, "messages": convert_to_messages(msgs)}
        return input

    async def _astream(input, *args, **kwargs):
        async for chunk in compiled.astream(_convert(input), *args, **kwargs):
            yield chunk

    async def _ainvoke(input, *args, **kwargs):
        return await compiled.ainvoke(_convert(input), *args, **kwargs)

    def _stream(input, *args, **kwargs):
        yield from compiled.stream(_convert(input), *args, **kwargs)

    def _invoke(input, *args, **kwargs):
        return compiled.invoke(_convert(input), *args, **kwargs)

    compiled.astream = _astream
    compiled.ainvoke = _ainvoke
    compiled.stream = _stream
    compiled.invoke = _invoke
    return compiled

"""LLM 调用层：节点级配置与解析。"""

from pydantic import BaseModel

# 模型常量集中在配置（core/config.py），此处只定义节点配置结构


class LLMConfig(BaseModel):
    """单节点 LLM 配置。temperature 刻意不提供：Claude 5 家族不支持采样参数。"""

    model: str | None = None  # None → settings.default_model
    effort: str = "high"  # low | medium | high | xhigh | max
    max_tokens: int = 8192
    stream: bool = True


DEFAULT_EFFORTS = {
    "executor": "high",
    "planner": "medium",
    "reflector": "medium",
    "finalizer": "medium",
    "memory_extractor": "medium",
    "judge": "medium",
}


def resolve_llm_config(node: str, node_overrides: dict | None) -> LLMConfig:
    """合并三层配置：全局默认 → settings.node_models[node] → agent.node_models[node]。"""
    from app.core.config import get_settings

    s = get_settings()
    base: dict = {
        "model": s.default_model,
        "effort": DEFAULT_EFFORTS.get(node, "medium"),
    }
    base.update(s.node_models.get(node, {}))
    base.update(node_overrides or {})
    return LLMConfig(**base)

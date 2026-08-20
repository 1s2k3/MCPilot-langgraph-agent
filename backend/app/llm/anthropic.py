"""Anthropic Provider（langchain-anthropic 1.x，已按安装版本验证字段）。

要点：
- Claude 5 家族：adaptive thinking + output_config.effort；
  不传 temperature/top_p/top_k（Opus 5 上会 400）
- stream_usage=True：节点内捕获每轮 token 用量
- max_retries=2：SDK 内置 429/5xx/网络重试；超时来自配置
"""

from typing import Any

from langchain_anthropic import ChatAnthropic

from app.core.config import get_settings
from app.llm.base import LLMConfig


def build_anthropic_model(cfg: LLMConfig) -> ChatAnthropic:
    s = get_settings()
    kwargs: dict[str, Any] = {
        "model": cfg.model or s.default_model,
        "max_tokens": cfg.max_tokens,
        "max_retries": 2,
        "default_request_timeout": s.llm_call_timeout_seconds,
        "stream_usage": cfg.stream,
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": cfg.effort},
    }
    if s.anthropic_api_key is not None:
        kwargs["anthropic_api_key"] = s.anthropic_api_key.get_secret_value()
    return ChatAnthropic(**kwargs)

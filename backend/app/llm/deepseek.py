"""DeepSeek Provider（OpenAI 兼容 API）。

要点：
- 默认 deepseek-v4-flash：支持 OpenAI 风格 function calling 与流式输出
- 结构化输出统一走 function_calling 方法（json_schema 模式兼容性因版本而异）
- Anthropic 专属参数（thinking/effort）不适用：LLMConfig.effort 被忽略
"""

from typing import Any

from langchain_openai import ChatOpenAI

from app.core.config import get_settings
from app.llm.base import LLMConfig


def build_deepseek_model(cfg: LLMConfig, api_key: str | None = None) -> ChatOpenAI:
    s = get_settings()
    key = api_key
    if key is None and s.deepseek_api_key is not None:
        key = s.deepseek_api_key.get_secret_value()
    kwargs: dict[str, Any] = {
        "model": cfg.model or s.deepseek_model,
        "api_key": key,
        "base_url": s.deepseek_base_url,
        "max_tokens": cfg.max_tokens,
        "timeout": s.llm_call_timeout_seconds,
        "max_retries": 2,
        "stream_usage": cfg.stream,
    }
    return ChatOpenAI(**kwargs)


def structured(model, schema):
    """DeepSeek 结构化输出：function_calling + tool_choice="auto"。

    deepseek-v4-flash 默认 thinking 模式，实测限制（2026-08 验证）：
    - 拒绝强制 tool_choice（对象形式，如 {"type":"function",...}）→ 400
    - 拒绝 response_format（json_object / json_schema）→ 400
    - 接受普通工具调用（tool_choice="auto" 或不设置）
    因此不强制模型调用工具：依赖调用方 prompt 强约束，解析失败由调用方兜底重试。
    """
    return model.with_structured_output(schema, method="function_calling", tool_choice="auto")

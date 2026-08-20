"""LangSmith 环境配置：仅在显式开启且配置了 API Key 时激活（平台功能不依赖它）。"""

import os

from app.core.config import Settings


def configure_tracing(settings: Settings) -> None:
    if not settings.langchain_tracing_v2 or settings.langchain_api_key is None:
        return
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key.get_secret_value()
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project

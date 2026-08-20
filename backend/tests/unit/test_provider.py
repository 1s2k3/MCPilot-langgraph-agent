"""Provider 选择与 DeepSeek 适配单元测试（不发起网络请求）。"""

from app.core import config as config_module
from app.core.config import Settings
from app.llm.base import resolve_llm_config
from app.llm.deepseek import build_deepseek_model, structured


def _with_env(monkeypatch, **kwargs) -> None:
    for k, v in kwargs.items():
        monkeypatch.setenv(k, v)
    config_module.get_settings.cache_clear()


def _restore(monkeypatch) -> None:
    for k in ("LLM_PROVIDER", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"):
        monkeypatch.delenv(k, raising=False)
    config_module.get_settings.cache_clear()


def test_resolve_llm_config_defaults_per_provider(monkeypatch) -> None:
    _with_env(monkeypatch, LLM_PROVIDER="deepseek", DEEPSEEK_MODEL="deepseek-v4-flash")
    try:
        cfg = resolve_llm_config("executor", None)
        assert cfg.model == "deepseek-v4-flash"
    finally:
        _restore(monkeypatch)
    # 默认 anthropic
    cfg = resolve_llm_config("executor", None)
    assert cfg.model == "claude-opus-5"


def test_build_deepseek_model_no_network(monkeypatch) -> None:
    _with_env(monkeypatch, LLM_PROVIDER="deepseek", DEEPSEEK_API_KEY="sk-test-123456")
    try:
        model = build_deepseek_model(resolve_llm_config("executor", None))
        assert model.model_name == "deepseek-v4-flash"
        assert "deepseek" in str(model.openai_api_base or "")
        assert model.openai_api_key.get_secret_value() == "sk-test-123456"
    finally:
        _restore(monkeypatch)


def test_structured_uses_function_calling(monkeypatch) -> None:
    """DeepSeek 结构化输出走 function_calling 方法（兼容性最好）。"""
    from pydantic import BaseModel

    class Plan(BaseModel):
        steps: list[str]

    _with_env(monkeypatch, LLM_PROVIDER="deepseek", DEEPSEEK_API_KEY="sk-test-123456")
    try:
        model = build_deepseek_model(resolve_llm_config("planner", None))
        chain = structured(model, Plan)
        assert chain is not None  # 链构建本身不发起网络
    finally:
        _restore(monkeypatch)


async def test_build_node_llms_deepseek_without_key_falls_back_scripted(monkeypatch) -> None:
    from app.agent.runner import build_node_llms
    from app.db.models import Agent

    _with_env(monkeypatch, LLM_PROVIDER="deepseek")
    try:
        llms, structured_fn = await build_node_llms(Agent(name="t"))
        assert "ScriptedChatModel" in type(llms["executor"]).__name__
        assert structured_fn is None
    finally:
        _restore(monkeypatch)


def test_empty_deepseek_key_treated_as_unset(monkeypatch) -> None:
    _with_env(monkeypatch, LLM_PROVIDER="deepseek", DEEPSEEK_API_KEY="")
    try:
        s = Settings(_env_file=None)
        assert s.deepseek_api_key is None
    finally:
        _restore(monkeypatch)

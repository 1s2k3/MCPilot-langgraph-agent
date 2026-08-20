"""配置单元测试：默认值与解析逻辑，不依赖数据库。"""

from app.core.config import Settings


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.default_model == "claude-opus-5"
    assert s.short_term_window == 20
    assert s.memory_top_k == 8
    assert s.embedding_dim == 384
    assert s.cors_origins == ["http://localhost:5173"]


def test_cors_split_from_env_string() -> None:
    s = Settings(_env_file=None, cors_origins="http://a.com, http://b.com")
    assert s.cors_origins == ["http://a.com", "http://b.com"]


def test_node_models_default_all_empty() -> None:
    s = Settings(_env_file=None)
    for node in ("planner", "executor", "reflector", "finalizer", "memory_extractor", "judge"):
        assert s.node_models[node] == {}

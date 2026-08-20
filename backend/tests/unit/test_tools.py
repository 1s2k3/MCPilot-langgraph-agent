"""工具层单元测试：脱敏 / 规范化 / 截断 / calculator 安全求值。"""

from app.tools.executor import (
    feedback_text,
    mask_secrets,
    normalize_result,
    store_result,
    to_json_safe,
)
from app.tools.local import _safe_eval, calculator, get_current_time


# ---- 脱敏 ----
def test_mask_secrets_nested_and_case_insensitive() -> None:
    data = {
        "api_key": "sk-abc",
        "nested": {"Authorization": "Bearer xyz", "ok": 1},
        "list": [{"password": "p", "name": "n"}],
    }
    masked = mask_secrets(data)
    assert masked["api_key"] == "***"
    assert masked["nested"]["Authorization"] == "***"
    assert masked["nested"]["ok"] == 1
    assert masked["list"][0]["password"] == "***"
    assert masked["list"][0]["name"] == "n"


def test_mask_secrets_leaves_plain_values() -> None:
    assert mask_secrets({"a": {"b": [1, 2, "x"]}}) == {"a": {"b": [1, 2, "x"]}}


def test_mask_secrets_scalar_value_patterns() -> None:
    # 标量值模式检测（安全评审发现 3 加固）
    assert mask_secrets({"data": "token sk-ant-abc1234567890xyz"}) == {"data": "***"}
    assert mask_secrets({"data": "ghp_abcdefghijklmnopqrstuvwxyz"}) == {"data": "***"}
    assert mask_secrets({"data": "Bearer eyJhbGciOiJIUzI1NiJ9.abc.def"}) == {"data": "***"}
    assert mask_secrets({"cookie": "session=abc", "session": "x", "auth": "y"}) == {
        "cookie": "***",
        "session": "***",
        "auth": "***",
    }
    # 普通文本不受影响
    assert mask_secrets({"data": "普通的一句话"}) == {"data": "普通的一句话"}


def test_mask_secrets_deep_structures_masked_not_passed() -> None:
    deep = {"k": "value"}
    for _ in range(12):
        deep = {"nested": deep}
    out = mask_secrets(deep)
    for _ in range(9):
        out = out["nested"]
    assert out == "***"


# ---- 规范化 ----
def test_normalize_str_result() -> None:
    assert normalize_result("hello") == {"ok": True, "data": "hello"}


def test_normalize_mcp_content_blocks() -> None:
    result = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    assert normalize_result(result) == {"ok": True, "data": "a\nb"}


def test_normalize_list_of_blocks() -> None:
    assert normalize_result([{"type": "text", "text": "x"}]) == {"ok": True, "data": "x"}


def test_to_json_safe_dict_keys_str() -> None:
    assert to_json_safe({1: "a"}) == {"1": "a"}


# ---- 截断 ----
def test_store_result_truncates_over_cap() -> None:
    payload, truncated = store_result({"ok": True, "data": "x" * 5000}, cap_bytes=100)
    assert truncated is True
    assert payload["truncated"] is True
    assert len(payload["preview"]) == 100


def test_store_result_within_cap() -> None:
    payload, truncated = store_result({"ok": True, "data": "small"}, cap_bytes=100)
    assert truncated is False
    assert payload == {"ok": True, "data": "small"}


def test_feedback_text_truncates_with_notice() -> None:
    text = feedback_text({"ok": True, "data": "y" * 1000}, cap_chars=50)
    assert len(text) <= 200
    assert "截断" in text


# ---- calculator 安全求值 ----
def test_calculator_basic() -> None:
    assert _safe_eval("1+2") == 3
    assert _safe_eval("(3+5)*7") == 56
    assert _safe_eval("2**10") == 1024
    assert _safe_eval("-3") == -3


def test_calculator_rejects_dangerous_input() -> None:
    for expr in ("__import__('os')", "open('/etc/passwd')", "x = 1", "1 + (2"):
        try:
            _safe_eval(expr)
            raise AssertionError(f"应当拒绝: {expr}")
        except ValueError:
            pass


def test_calculator_dos_limits() -> None:
    # DoS 防护（安全评审发现 5）：指数界检查先于求值，禁止嵌套幂
    for expr in ("9**9**9", "2**2000", "9" * 60):
        try:
            _safe_eval(expr)
            raise AssertionError(f"应当拒绝: {expr[:20]}…")
        except ValueError:
            pass
    # 合法边界仍可用
    assert _safe_eval("2**10") == 1024


def test_calculator_division_by_zero_is_error() -> None:
    assert "失败" in calculator.invoke("1/0")


def test_calculator_tool_returns_str() -> None:
    assert calculator.invoke("2*3") == "6"


def test_get_current_time_iso() -> None:
    out = get_current_time.invoke({})
    assert isinstance(out, str)
    assert out.endswith("+00:00")

"""权限策略引擎单元测试（§5.9）。"""

from app.tools.policy import resolve_tool_action


def test_exact_match() -> None:
    policy = {"rules": [{"tool": "calculator", "action": "ask"}], "default": "allow"}
    assert resolve_tool_action(policy, "calculator") == "ask"
    assert resolve_tool_action(policy, "get_current_time") == "allow"


def test_server_wildcard() -> None:
    policy = {"rules": [{"tool": "mcp-demo.*", "action": "ask"}], "default": "allow"}
    assert resolve_tool_action(policy, "mcp-demo_math_add", "mcp-demo") == "ask"
    assert resolve_tool_action(policy, "calculator") == "allow"


def test_global_wildcard_and_default() -> None:
    policy = {"rules": [{"tool": "*", "action": "ask"}], "default": "deny"}
    assert resolve_tool_action(policy, "anything") == "ask"
    policy2 = {"rules": [], "default": "deny"}
    assert resolve_tool_action(policy2, "calculator") == "deny"


def test_rule_order_first_match_wins() -> None:
    policy = {
        "rules": [
            {"tool": "mcp-demo.*", "action": "allow"},
            {"tool": "mcp-demo_math_add", "action": "deny"},
        ],
        "default": "ask",
    }
    assert resolve_tool_action(policy, "mcp-demo_math_add", "mcp-demo") == "allow"


def test_empty_policy_defaults_allow() -> None:
    assert resolve_tool_action(None, "calculator") == "allow"
    assert resolve_tool_action({}, "calculator") == "allow"

"""工具权限策略引擎（§5.9）：allow / ask / deny + 通配符。

策略结构（agents.tool_policy JSONB）：
    {"rules": [{"tool": "calculator", "action": "ask"},
               {"tool": "mcp-demo.*", "action": "allow"}],
     "default": "ask"}

语义：
- deny：绑定阶段即从 LLM 工具列表隐藏（隐藏优于执行时拦截）
- ask：执行前 interrupt 请求人工审批；会话级授权缓存在 state.tool_approvals
"""


def resolve_tool_action(tool_policy: dict | None, tool_name: str, server: str = "local") -> str:
    """解析工具动作：规则按声明顺序匹配，未命中走 default。"""
    policy = tool_policy or {}
    for rule in policy.get("rules") or []:
        pattern = rule.get("tool") or ""
        if _matches(pattern, tool_name, server):
            return rule.get("action") or "allow"
    return policy.get("default") or "allow"


def _matches(pattern: str, tool_name: str, server: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):  # server 级通配："mcp-demo.*" 匹配前缀 "mcp-demo_"
        prefix = pattern[:-2]
        return tool_name.startswith(prefix) or server == prefix
    return pattern == tool_name

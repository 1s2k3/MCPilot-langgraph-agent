"""本地内置工具（v1 最小集）：calculator / get_current_time。

calculator 用 AST 白名单求值：只允许数字与 + - * / ** 运算，禁止函数调用与变量 —— 无 eval 风险。
"""

import ast
import operator
from datetime import UTC, datetime

from langchain_core.tools import tool

_ALLOWED_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY_OPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}


def _safe_eval(expression: str) -> float:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"表达式语法错误: {exc.msg}") from exc

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BIN_OPS:
            return _ALLOWED_BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY_OPS:
            return _ALLOWED_UNARY_OPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"不允许的表达式元素: {type(node).__name__}")

    try:
        return _eval(tree)
    except (ZeroDivisionError, OverflowError) as exc:
        raise ValueError(f"运算错误: {exc}") from exc


@tool
def calculator(expression: str) -> str:
    """安全计算数学表达式（仅数字与 + - * / ** 运算，禁止函数调用与变量）。"""
    try:
        return str(_safe_eval(expression))
    except ValueError as exc:
        return f"计算失败: {exc}"


@tool
def get_current_time() -> str:
    """返回当前 UTC 时间（ISO8601 格式）。"""
    return datetime.now(UTC).isoformat()

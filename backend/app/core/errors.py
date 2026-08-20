"""统一错误模型：API 返回信封 {"error": {code, message, retryable, details}}。"""

from typing import Any


class AppError(Exception):
    """业务/运行错误，由全局异常处理器转换为统一信封。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        self.status_code = status_code

    def to_envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            }
        }


def not_found(resource: str, key: str | None = None) -> AppError:
    msg = f"{resource} 不存在" if key is None else f"{resource} 不存在: {key}"
    return AppError("not_found", msg, status_code=404)

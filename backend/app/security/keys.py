"""密钥安全（§9）：Fernet 对称加密 + 掩码展示。

- APP_MASTER_KEY 仅在部署环境变量中存在，密钥只以密文落库
- API 响应永远只返回掩码，写后不可读
"""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.errors import AppError


def _fernet() -> Fernet:
    key = get_settings().app_master_key
    if key is None:
        raise AppError(
            "master_key_missing",
            "未配置 APP_MASTER_KEY，密钥存储功能不可用（python scripts/gen_master_key.py 生成）",
            status_code=500,
        )
    return Fernet(key.get_secret_value().encode())


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise AppError(
            "decrypt_failed", "密钥解密失败（APP_MASTER_KEY 是否变更？）", status_code=500
        ) from exc


def mask_key(secret: str, keep: int = 4) -> str:
    """掩码展示：sk-…abcd（长度不足时全掩）。"""
    if len(secret) <= keep + 4:
        return "***"
    return f"{secret[:keep]}…{secret[-4:]}"

"""密钥安全（§9）：Fernet 对称加密 + 掩码展示。

- APP_MASTER_KEY 仅在部署环境变量中存在，密钥只以密文落库
- API 响应永远只返回掩码，写后不可读
"""

from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.models import ApiKey
from app.db.session import SessionLocal

logger = get_logger(__name__)


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


async def get_provider_key(provider: str) -> str | None:
    """读取 provider 的最新可用密钥（解密 + 刷新 last_used_at）；无密钥/解密失败 → None。"""
    async with SessionLocal() as session:
        row = (
            (
                await session.execute(
                    select(ApiKey)
                    .where(ApiKey.provider == provider)
                    .order_by(ApiKey.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
    if row is None:
        return None
    try:
        plain = decrypt_secret(row.key_ciphertext)
    except AppError:
        logger.warning("provider_key_decrypt_failed", provider=provider, key_id=str(row.id))
        return None
    async with SessionLocal() as session:
        db_row = await session.get(ApiKey, row.id)
        if db_row is not None:
            db_row.last_used_at = datetime.now(UTC)
            await session.commit()
    return plain


__all__ = ["encrypt_secret", "decrypt_secret", "mask_key", "get_provider_key"]

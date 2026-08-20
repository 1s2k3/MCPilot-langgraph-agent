"""密钥安全单元测试：Fernet 加解密 + 掩码。"""

from cryptography.fernet import Fernet

from app.core import config as config_module
from app.security.keys import decrypt_secret, encrypt_secret, mask_key


def _with_master_key(monkeypatch) -> None:
    monkeypatch.setenv("APP_MASTER_KEY", Fernet.generate_key().decode())
    config_module.get_settings.cache_clear()


def _restore(monkeypatch) -> None:
    monkeypatch.delenv("APP_MASTER_KEY", raising=False)
    config_module.get_settings.cache_clear()


def test_encrypt_decrypt_roundtrip(monkeypatch) -> None:
    _with_master_key(monkeypatch)
    try:
        ciphertext = encrypt_secret("sk-ant-super-secret")
        assert "sk-ant-super-secret" not in ciphertext
        assert decrypt_secret(ciphertext) == "sk-ant-super-secret"
    finally:
        _restore(monkeypatch)


def test_decrypt_rejects_wrong_key(monkeypatch) -> None:
    from app.core.errors import AppError

    _with_master_key(monkeypatch)
    try:
        ciphertext = encrypt_secret("value")
        monkeypatch.setenv("APP_MASTER_KEY", Fernet.generate_key().decode())
        config_module.get_settings.cache_clear()
        try:
            decrypt_secret(ciphertext)
            raise AssertionError("应当解密失败")
        except AppError as exc:
            assert exc.code == "decrypt_failed"
    finally:
        _restore(monkeypatch)


def test_mask_key() -> None:
    assert mask_key("sk-ant-abcdef123456") == "sk-a…3456"
    assert mask_key("short") == "***"

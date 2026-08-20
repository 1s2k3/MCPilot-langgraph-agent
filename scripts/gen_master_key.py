"""生成 Fernet 主密钥，写入 .env 的 APP_MASTER_KEY。

用法: python scripts/gen_master_key.py
"""
from cryptography.fernet import Fernet

print(Fernet.generate_key().decode())

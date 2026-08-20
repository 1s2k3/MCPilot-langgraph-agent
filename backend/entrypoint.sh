#!/bin/sh
# 迁移 → 启动（Linux 容器无需自定义事件循环）
set -e
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

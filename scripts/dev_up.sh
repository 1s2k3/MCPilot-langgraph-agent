#!/usr/bin/env bash
# 开发模式：只起基础设施（PostgreSQL / 演示 MCP server 由后端 stdio 拉起）
set -e
cd "$(dirname "$0")/.."
docker compose up -d postgres
echo "PostgreSQL 已启动 (localhost:5432)。后端: cd backend && alembic upgrade head && .venv/Scripts/python -m uvicorn app.main:app --reload"

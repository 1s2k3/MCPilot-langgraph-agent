# MCP Agent Platform

生产级单 Agent 平台：**MCP 工具接入 + Planning + Memory + Evaluation + Observability**。

架构与开发计划见 [docs/development-framework.md](docs/development-framework.md)。

## 组件

| 目录 | 说明 |
|---|---|
| `backend/` | Python 3.12 + FastAPI + LangGraph（Agent Runtime、MCP 客户端、Checkpoint、Memory、Evaluation） |
| `frontend/` | React 18 + Vite + Tailwind（Chat 流式、Timeline、State Inspector、Eval Dashboard） |
| `mcp-demo/` | 演示 MCP server（math / time / echo，只读） |
| `docs/` | 设计文档 |

## 快速开始（开发中，M8 前补全）

```bash
# 1. 启动 PostgreSQL（需 Docker）
docker compose up -d postgres

# 2. 后端
cd backend
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Windows
alembic upgrade head
# Windows 需指定 selector 事件循环（psycopg 异步要求）；Linux 可省略 --loop
.venv/Scripts/python -m uvicorn app.main:app --reload --loop app.server:loop_factory

# 3. 前端
cd frontend && npm install && npm run dev
```

环境变量复制 `.env.example` 为 `.env` 后填写。

# MCP Agent Platform

生产级单 Agent 平台：**MCP 工具接入 + Planning + Memory + Evaluation + Observability**。

架构设计与开发计划见 [docs/development-framework.md](docs/development-framework.md)。

## 组件

| 目录 | 说明 |
|---|---|
| `backend/` | Python 3.12 + FastAPI + LangGraph（Agent Runtime、MCP 客户端、Postgres Checkpoint、Memory、Evaluation） |
| `frontend/` | React 18 + Vite + Tailwind + Recharts（Chat 流式、Timeline、State Inspector、Eval Dashboard） |
| `mcp-demo/` | 演示 MCP server（math / time / echo，只读；stdio 或 HTTP 模式） |
| `docs/` | 设计文档 |
| `scripts/` | 运维脚本（密钥生成、seed 数据集、开发环境启动） |

## 快速开始（Docker Compose，推荐）

```bash
cp .env.example .env            # 填写 APP_MASTER_KEY（scripts/gen_master_key.py 生成）与 ANTHROPIC_API_KEY
docker compose up -d --build
# Web UI:        http://localhost:5174
# API 文档:      http://localhost:8000/docs
# 装载评估数据集:
python scripts/seed_dataset.py http://localhost:8000
```

未配置 ANTHROPIC_API_KEY 时平台以 **scripted 演示模式**运行（离线可完整体验 Agent 循环 / MCP / 评估全流程）。

## 本地开发

```bash
# 1. 基础设施（需 Docker）
docker compose up -d postgres mcp-demo

# 2. 后端（Windows 需指定 selector 事件循环；Linux 省略 --loop）
cd backend
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Windows
alembic upgrade head
.venv/Scripts/python -m uvicorn app.main:app --reload --loop app.server:loop_factory

# 3. 前端
cd frontend && npm install && npm run dev   # http://localhost:5173（代理 /api → :8000）
```

### 测试

```bash
cd backend
.venv/Scripts/python -m pytest tests/unit -q          # 单元测试（无外部依赖，ScriptedLLM 确定性驱动）
.venv/Scripts/python -m pytest tests/integration -q   # 集成测试（需 PostgreSQL + mcp-demo；本地无 DB 自动跳过）
.venv/Scripts/python -m ruff check app tests          # lint
```

CI 门禁（GitHub Actions）：lint + unit + integration（pgvector 服务 + fastembed 缓存 + 评估回归 `test_eval`）。

## 核心能力

- **Agent Loop**（LangGraph）：`load_context → planner → executor ⇄ tools → reflector → finalizer`
  - 结构化计划（步骤状态机）、反思修正（retry 注入反馈 / 重试耗尽重规划 / abort 降级）、预算护栏
- **MCP 工具**：多服务器（stdio / streamable_http）、`server_` 前缀命名空间、失联降级、连接测试
- **State / Checkpoint**：Postgres 持久化（`AsyncPostgresSaver`），线程恢复、崩溃续跑、会话级工具授权
- **Memory**：短期 = 消息窗口 + 滚动摘要；长期 = fastembed（本地）+ pgvector 语义检索，运行后自动提取 + Agent 主动读写工具
- **Permission / HITL**：`allow / ask / deny` + 通配符策略；ask 触发审批挂起 → 前端弹窗 → resume API
- **Observability**：SSE 实时流 + 事件表回放（Timeline / State Inspector / 工具卡 / 记忆溯源）；LangSmith 可选全链路 trace
- **Evaluation**：数据集回放、轨迹捕获、确定性指标（工具序列匹配 / 成功率 / 反思修正率）、LLM Judge 1-5 评分、Dashboard

## API 文档

- OpenAPI（自动生成）：`GET /docs`（`/api/*` 全部端点）
- SSE 协议与事件契约：见 `docs/development-framework.md` §7.2

## 安全

- **网关鉴权**：配置 `ADMIN_TOKEN` 后 `/api/*` 强制校验（请求头 `X-Admin-Token` 或查询参数 `admin_token`，SSE 兼容）；前端 401 自动提示输入
- API Key、MCP headers/env 一律 Fernet 加密落库，**写后不可读**，响应只返回掩码（不泄漏前缀）
- **MCP 入口防护**：stdio command 可执行白名单（`MCP_COMMAND_ALLOWLIST`）；streamable_http 仅 http(s) 且默认拒绝回环/私有/保留地址（`ALLOW_PRIVATE_MCP_URLS` 仅供本地开发豁免）
- 工具参数/结果脱敏（敏感键 + 常见密钥形态标量检测 + 超深结构打码）贯穿**落库、回喂 LLM、checkpoint、事件回放**四条链路；`deny` 工具在绑定阶段隐藏（隐藏优于拦截）
- calculator AST 白名单求值 + 长度/操作数/指数界检查（防大整数 DoS）
- `LANGGRAPH_STRICT_MSGPACK=true` checkpoint 反序列化防护；错误响应只返回固定文案（异常细节仅进服务端日志）
- 工具结果标注为不可信外部数据（提示注入缓解）；v1 工具面只读（无文件写/删）

## 环境变量

见 `.env.example`（数据库 / 主密钥 / LLM / LangSmith / embedding / 脚本化模式 / CORS）。

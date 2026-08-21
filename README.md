# MCP Agent Platform

生产级单 Agent 平台:**MCP 工具接入 + Planning + Memory + Evaluation + Observability** 一站式。
真实 LLM 与离线 scripted 演示双模式,开箱即跑;Docker Compose 一键起栈,LangSmith Studio 可视化调试。

架构设计与开发计划见 [docs/development-framework.md](docs/development-framework.md)。

## 亮点

- **开箱即跑**:未配置任何 API Key 时以 **scripted 演示模式**离线跑通完整 Agent 循环 / MCP / 评估全流程;配 Key 即切真实 LLM,零代码改动。
- **Plan-Execute-Reflect 闭环**:结构化步骤计划 + 反思修正(retry 注入反馈 / 重试耗尽重规划 / abort 降级),带预算护栏防失控。
- **两层记忆**:短期(消息窗口 + 滚动摘要)+ 长期(本地 fastembed + pgvector 语义检索,运行后自动提取 + Agent 主动读写),跨会话记住用户。
- **MCP 原生**:多服务器(stdio / streamable_http)、`server_` 前缀命名空间、失联降级、连接测试、工具白名单;凭据 Fernet 加密落库。
- **Human-in-the-Loop**:`allow / ask / deny` + 通配符权限;ask 触发审批挂起 → 前端弹窗 → resume 续跑,会话级授权记忆。
- **可观测**:SSE 实时流 + 事件表回放(Timeline / State Inspector / 工具卡 / 记忆溯源);LangSmith 全链路 trace 可选。
- **评估闭环**:数据集回放 + 轨迹捕获 + 确定性指标(工具序列匹配 / 成功率 / 反思修正率)+ LLM Judge 1-5 评分 + Dashboard。
- **安全纵深**:网关鉴权、凭据加密写后不可读、MCP 入站防护(command 白名单 + SSRF)、脱敏贯穿四条链路、calculator AST 白名单、checkpoint 反序列化防护。
- **可视化调试**:LangGraph Studio(Docker profile / 本地 dev)图结构 + Time Travel + 热重载。

## 组件

| 目录 | 说明 |
|---|---|
| `backend/` | Python 3.12 + FastAPI + LangGraph(Agent Runtime、MCP 客户端、Postgres Checkpoint、Memory、Evaluation) |
| `frontend/` | React 18 + Vite + Tailwind + Recharts(Chat 流式、Timeline、State Inspector、Eval Dashboard) |
| `mcp-demo/` | 演示 MCP server(math / time / echo,只读;stdio 或 HTTP 模式) |
| `docs/` | 设计文档 |
| `scripts/` | 运维脚本(密钥生成、seed 评估数据集) |

## 快速开始(Docker Compose,推荐)

```bash
cp .env.example .env            # 填写 APP_MASTER_KEY(scripts/gen_master_key.py 生成)
                                # LLM:LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY
                                #   或 LLM_PROVIDER=deepseek + DEEPSEEK_API_KEY
docker compose up -d --build
# Web UI:     http://localhost:5174
# API 文档:   http://localhost:8000/docs
# 装载评估数据集(可选):
python scripts/seed_dataset.py http://localhost:8000
```

未配置任何 API Key 时平台以 **scripted 演示模式**运行(离线可完整体验 Agent 循环 / MCP / 评估全流程)。

## 本地开发

```bash
# 1. 基础设施(需 Docker)
docker compose up -d postgres mcp-demo

# 2. 后端(Windows 需指定 selector 事件循环;Linux 省略 --loop)
cd backend
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Windows
alembic upgrade head
.venv/Scripts/python -m uvicorn app.main:app --reload --loop app.server:loop_factory

# 3. 前端
cd frontend && npm install && npm run dev   # http://localhost:5173(代理 /api → :8000)
```

### 测试

```bash
cd backend
.venv/Scripts/python -m pytest tests/unit -q          # 单元测试(无外部依赖,ScriptedLLM 确定性驱动)
.venv/Scripts/python -m pytest tests/integration -q   # 集成测试(需 PostgreSQL + mcp-demo;本地无 DB 自动跳过)
.venv/Scripts/python -m ruff check app tests          # lint
```

CI 门禁(GitHub Actions):lint + unit + integration(pgvector 服务 + fastembed 缓存 + 评估回归 `test_eval`)。

## 核心能力

### Agent Loop(LangGraph)

`load_context → planner → executor ⇄ tools → reflector → finalizer`

- **结构化计划**:planner 产出步骤状态机(`pending → in_progress → done/failed`),每步带 `tools_hint`。
- **反思修正**:reflector 输出 `pass / retry(注入反馈) / replan(重规划) / abort(降级终态)`;retry 耗尽自动重规划。
- **预算护栏**:`max_llm_calls / max_total_tool_calls / max_plan_steps / max_attempts_per_step` 防失控。
- **超窗压缩**:消息超 `short_term_window` 时溢出部分压成滚动摘要回喂,长对话不爆上下文。

### MCP 工具

- 多服务器,`stdio` 与 `streamable_http` 双传输;工具名带 `<server>_` 前缀命名空间。
- 失联降级(单 server 故障不拖垮平台,health 标 error)+ 连接测试(不碰主会话池)。
- 工具白名单(`tool_allowlist`)只暴露部分工具;凭据(headers/env)Fernet 加密落库,响应仅掩码。

### State / Checkpoint

- Postgres 持久化(`AsyncPostgresSaver`),按 `thread_id` 续接;崩溃续跑、会话级工具授权随 checkpoint 持久化。
- HITL:`interrupt` 挂起 → 等待 `resume`(Command)→ 继续;无 checkpoint 时 ask 策略明确失败而非静默拒绝。

### Memory(两层)

- **短期**:`state.messages`(窗口)+ `state.summary`(超窗滚动摘要),checkpointer 按 thread 持久化。
- **长期**:`memories` 表(pgvector 384 维),本地 fastembed(all-MiniLM-L6-v2,零外部 API)余弦相似度 top-k。
  - 读:`load_context` 节点检索注入 planner/executor。
  - 写①:run 结束 fire-and-forget 提取(LLM 结构化 → 去重 upsert → embedding)。
  - 写②:Agent 主动 `remember_memory` / `forget_memory`(语义删 top-1,阈值 0.7)。
  - 管理:`/api/memories` 列表 / 搜索 / 编辑 / 删除(前端 Memory 面板)。
- `EMBEDDING_PROVIDER=disabled` 时整体关闭长期记忆检索(best-effort,不影响主流程)。

### Permission / HITL

- `allow / ask / deny` + 通配符策略;`deny` 工具在绑定阶段隐藏(隐藏优于执行时拦截)。
- `ask` → 审批挂起 → 前端弹窗 → resume API;支持会话级授权(`session_wide` 一次放行后续免问)。

### Observability

- SSE 实时流 + 事件表回放:Timeline、State Inspector、ToolCallCard、记忆溯源全链路可视化。
- LangSmith 可选:`.env` 配 `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY`,Web 端查看 Messages / Turns / Details 三视图 trace。

### Evaluation

- 数据集回放 + 轨迹捕获;确定性指标(工具序列匹配 / 成功率 / 反思修正率)+ LLM Judge 1-5 评分。
- Eval Dashboard 展示指标与单条轨迹;CI 内 `test_eval` 回归守底线。

### LLM Provider

- `anthropic`(Claude)、`deepseek`、`scripted`(离线确定性,演示/测试用);provider 适配结构化输出(DeepSeek 走 function_calling)。

## LangSmith Studio(执行过程可视化)

数据链路:真实 LLM 调用自动上报到 LangSmith(`.env` 配 `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY`),Web 端查看全部 trace(Messages / Turns / Details):

1. 浏览器打开 [smith.langchain.com](https://smith.langchain.com),登录后进入 **Projects → agent-platform**
2. 点任意 run:Messages 视图看对话轨迹,Details 视图看每节点输入/输出/耗时/Token

交互式调试(图结构 + Time Travel + 热重载):

```bash
cd backend
./.venv/Scripts/python -m pip install "langgraph-cli[inmem]"
./.venv/Scripts/langgraph dev --host 127.0.0.1 --port 2025
# 浏览器打开 https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2025
```

入口为 `backend/app/agent/studio.py`(真实 LLM + 内置/MCP 工具 + 权限默认 allow),`backend/langgraph.json` 声明图与 `.env` 位置。仅用于本地调试,不影响生产 FastAPI 路径。

### Docker Desktop 一键启动 Studio

`docker-compose.yml` 内置 `studio` 服务(独立 `Dockerfile.studio`,预装 `langgraph-cli[inmem]`),默认不随 `docker compose up` 启动,按需用 profile 拉起:

```bash
docker compose --profile studio up -d --build
# 浏览器打开 https://smith.langchain.com/studio/?baseUrl=http://localhost:2024
# 端口可调:在 .env 设 LANGGRAPH_STUDIO_PORT
```

Studio 容器用内存 Checkpointer(不写生产 DB),仅从 Postgres 读 MCP 配置并连 `mcp-demo`;`.env` 只读挂载,改 Key 后重启容器即生效,无需重建。

## 安全

- **网关鉴权**:配置 `ADMIN_TOKEN` 后 `/api/*` 强制校验(请求头 `X-Admin-Token` 或查询参数 `admin_token`,SSE 兼容);前端 401 自动提示输入。
- API Key、MCP headers/env 一律 Fernet 加密落库,**写后不可读**,响应只返回掩码(不泄漏前缀)。
- **MCP 入站防护**:stdio command 可执行白名单(`MCP_COMMAND_ALLOWLIST`);streamable_http 仅 http(s) 且默认拒绝回环/私有/保留地址(`ALLOW_PRIVATE_MCP_URLS` 仅供本地开发豁免)。
- 工具参数/结果脱敏(敏感键 + 常见密钥形态标量检测 + 超深结构打码)贯穿**落库、回喂 LLM、checkpoint、事件回放**四条链路;`deny` 工具绑定阶段隐藏。
- calculator AST 白名单求值 + 长度/操作数/指数界检查(防大整数 DoS)。
- `LANGGRAPH_STRICT_MSGPACK=true` checkpoint 反序列化防护;错误响应只返回固定文案(异常细节仅进服务端日志)。
- 工具结果标注为不可信外部数据(提示注入缓解);v1 工具面只读(无文件写/删)。

## API 文档

- OpenAPI(自动生成):`GET /docs`(`/api/*` 全部端点)
- SSE 协议与事件契约:见 [docs/development-framework.md](docs/development-framework.md) §7.2

## 环境变量

见 [.env.example](.env.example)(数据库 / 主密钥 / LLM / LangSmith / embedding / 脚本化模式 / CORS / Studio 端口)。

## 项目结构

```
.
├── backend/                 # FastAPI + LangGraph Runtime
│   ├── app/
│   │   ├── agent/           # graph / runner / state / studio / checkpoint
│   │   ├── api/             # agents / runs / threads / tools / mcp / memory / eval / keys / health
│   │   ├── core/            # config / errors / logging / tracing
│   │   ├── db/              # models / session / base
│   │   ├── eval/            # judge / runner
│   │   ├── events/          # bus / models
│   │   ├── llm/             # anthropic / deepseek / scripted
│   │   ├── memory/          # embedder / extractor / retriever
│   │   ├── security/        # keys (Fernet)
│   │   └── tools/           # executor / local / mcp_client / memory_tools / policy / registry
│   ├── alembic/             # 迁移(pgvector + 加密列)
│   ├── tests/               # unit + integration
│   ├── Dockerfile / Dockerfile.studio / langgraph.json / entrypoint.sh
│   └── pyproject.toml
├── frontend/                # React + Vite + Tailwind + Recharts
│   └── src/{pages,components,hooks,api}
├── mcp-demo/                # 演示 MCP server
├── scripts/                 # gen_master_key.py / seed_dataset.py
├── docs/                    # development-framework.md
└── docker-compose.yml       # postgres / mcp-demo / api / web / studio(profile)
```

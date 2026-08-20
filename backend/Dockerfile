FROM python:3.12-slim

WORKDIR /app

# 层序优化：依赖安装与 embedding 模型预热只依赖 pyproject + memory/core 模块，
# 后续 app 代码变更不会重新下载模型（层缓存命中）
COPY pyproject.toml ./
COPY app/memory ./app/memory
COPY app/core ./app/core
RUN pip install --no-cache-dir -e .
RUN python -c "from app.memory.embedder import embed; embed(['warmup'])"

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]

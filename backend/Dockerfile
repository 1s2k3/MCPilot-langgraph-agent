FROM python:3.12-slim

WORKDIR /app

# 先装依赖（利用层缓存）
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e . 2>/dev/null || true

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .

# 预下载 embedding 模型（打镜像，避免容器启动时下载）
RUN python -c "from app.memory.embedder import embed; embed(['warmup'])"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]

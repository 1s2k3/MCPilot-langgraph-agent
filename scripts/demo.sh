#!/usr/bin/env bash
# M8 验收：一键演示全流程（compose 起栈 → seed 数据集 → 跑一轮 agent → 评估）
set -e
cd "$(dirname "$0")/.."
API="${API_URL:-http://localhost:8000}"

echo "==> 1. 启动服务栈"
docker compose up -d --build
echo "==> 等待 api 就绪…"
for i in $(seq 1 60); do
  if curl -sf "$API/api/health" > /dev/null; then break; fi
  sleep 2
done
curl -s "$API/api/health"

echo "==> 2. 装载 seed 评估数据集"
python scripts/seed_dataset.py "$API" --force

echo "==> 3. 创建演示线程并跑一轮 agent（计算类任务，含计划/工具/反思全链路）"
THREAD=$(curl -s -X POST "$API/api/threads" -H "Content-Type: application/json" -d '{}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
RUN=$(curl -s -X POST "$API/api/threads/$THREAD/runs" -H "Content-Type: application/json" \
  -d '{"input": "帮我计算 1+2"}' | python -c "import sys,json;print(json.load(sys.stdin)['run_id'])")
echo "线程: $THREAD  运行: $RUN"

for i in $(seq 1 30); do
  STATUS=$(curl -s "$API/api/threads/$THREAD/runs/$RUN" | python -c "import sys,json;print(json.load(sys.stdin)['status'])")
  echo "  状态: $STATUS"
  [ "$STATUS" = "completed" ] && break
  sleep 2
done

echo "==> 4. 事件时间线（类型序列）"
curl -s "$API/api/threads/$THREAD/runs/$RUN/events" | python -c "
import sys, json
types = [e['type'] for e in json.load(sys.stdin)['events']]
print(' → '.join(types))
"

echo "==> 5. 运行评估（scripted 确定性回放）"
DATASET=$(curl -s "$API/api/eval/datasets" | python -c "
import sys, json
ds = json.load(sys.stdin)['datasets']
print(next(d['id'] for d in ds if d['name'] == '回归基线 v1'))")
EVAL=$(curl -s -X POST "$API/api/eval/datasets/$DATASET/runs" -H "Content-Type: application/json" -d '{}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['eval_run_id'])")
for i in $(seq 1 60); do
  METRICS=$(curl -s "$API/api/eval/runs/$EVAL")
  STATUS=$(echo "$METRICS" | python -c "import sys,json;print(json.load(sys.stdin)['status'])")
  [ "$STATUS" = "completed" ] && break
  sleep 2
done
echo "评估指标:"; echo "$METRICS" | python -m json.tool | head -30

echo "==> 完成。Web UI: http://localhost:5174 | API 文档: $API/docs"

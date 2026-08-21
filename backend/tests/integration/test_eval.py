"""M7 验收：评估全链路（真实 PG + ScriptedLLM 确定性回放 + 指标聚合）。"""

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_DATASET = {
    "name": "回归基线 v1",
    "description": "M7 验收数据集",
    "entries": [
        {
            "input": "帮我计算 1+2",
            "expected_tool_calls": ["calculator"],
            "reference_answer": "结果是 3",
            "rubric": "计算正确且给出结果",
            "category": "tool-usage",
            "difficulty": 1,
        },
        {
            "input": "记住 我最喜欢的颜色是蓝色",
            "expected_tool_calls": ["remember_memory"],
            "reference_answer": "已记住",
            "rubric": "调用记忆工具写入",
            "category": "memory",
            "difficulty": 1,
        },
        {
            "input": "你好",
            "expected_tool_calls": [],
            "reference_answer": None,
            "rubric": "正常回复即可",
            "category": "chat",
            "difficulty": 1,
        },
    ],
}


async def _wait_eval_done(client: AsyncClient, eval_run_id: str, timeout_s: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        run = (await client.get(f"/api/eval/runs/{eval_run_id}")).json()
        if run["status"] in ("completed", "failed"):
            return run
        await asyncio.sleep(0.3)
    raise AssertionError(f"评估未在 {timeout_s}s 内完成")


async def _cleanup_residuals(client: AsyncClient) -> None:
    """幂等：上次中断/失败运行的残留同名资源先清掉（agent 与数据集）。"""
    for d in (await client.get("/api/eval/datasets")).json()["datasets"]:
        if d["name"] == _DATASET["name"]:
            await client.delete(f"/api/eval/datasets/{d['id']}")
    for a in (await client.get("/api/agents")).json():
        if a["name"] == "评估目标 Agent":
            await client.delete(f"/api/agents/{a['id']}")


async def test_eval_pipeline(db_available) -> None:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _cleanup_residuals(client)
        resp = await client.post("/api/agents", json={"name": "评估目标 Agent"})
        agent_id = resp.json()["id"]

        resp = await client.post("/api/eval/datasets", json=_DATASET)
        assert resp.status_code == 201, resp.text
        dataset_id = resp.json()["id"]
        assert resp.json()["entry_count"] == 3

        resp = await client.post(
            f"/api/eval/datasets/{dataset_id}/runs", json={"agent_id": agent_id}
        )
        assert resp.status_code == 201, resp.text
        eval_run_id = resp.json()["eval_run_id"]

        run = await _wait_eval_done(client, eval_run_id)
        assert run["status"] == "completed", run
        metrics = run["metrics"]
        # 3 条全部成功（scripted 确定性）
        assert metrics["total"] == 3
        assert metrics["completed"] == 3
        assert metrics["success_rate"] == 1.0
        # 工具序列匹配：计算/记住 两条有期望序列且全部命中
        assert metrics["tool_seq_exact_rate"] == 1.0
        assert metrics["tool_seq_prefix_rate"] == 1.0
        # scripted 模式 judge 跳过
        assert metrics["judged"] == 0
        assert metrics["judge_skipped"] == 3
        # 轨迹字段存在
        assert metrics["avg_latency_ms"] is not None
        assert metrics["avg_iterations"] is not None

        # 逐条评分：轨迹含工具序列
        scores = (await client.get(f"/api/eval/runs/{eval_run_id}/scores")).json()["scores"]
        assert len(scores) == 3
        by_input = {s["input"]: s for s in scores}
        assert by_input["帮我计算 1+2"]["trajectory"]["tool_sequence"] == ["calculator"]
        assert by_input["记住 我最喜欢的颜色是蓝色"]["trajectory"]["tool_sequence"] == [
            "remember_memory"
        ]
        assert by_input["你好"]["trajectory"]["tool_sequence"] == []

        # 运行记录列表
        runs = (await client.get("/api/eval/runs")).json()["runs"]
        assert any(r["id"] == eval_run_id for r in runs)

        # 数据集删除（级联清理评估）
        assert (await client.delete(f"/api/eval/datasets/{dataset_id}")).status_code == 204

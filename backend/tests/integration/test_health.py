"""健康检查集成测试（M0 验收：compose up 后 /api/health 200）。"""

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


async def test_health_db_ok(db_available) -> None:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"

"""装载 seed 评估数据集到平台 API。

用法:
    python scripts/seed_dataset.py [API_URL] [--force]

默认 API_URL=http://localhost:8000；--force 先删除同名数据集再创建。
"""
import json
import sys
from pathlib import Path

import httpx

API_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
FORCE = "--force" in sys.argv
SEED = Path(__file__).resolve().parents[1] / "backend" / "tests" / "datasets" / "seed_v1.json"


def main() -> None:
    data = json.loads(SEED.read_text(encoding="utf-8"))
    name = data["name"]
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        if FORCE:
            existing = client.get("/api/eval/datasets").json().get("datasets", [])
            for d in existing:
                if d["name"] == name:
                    print(f"删除已有数据集 {name}:", client.delete(f"/api/eval/datasets/{d['id']}").status_code)
        resp = client.post(
            "/api/eval/datasets",
            json={"name": name, "description": data["description"], "entries": data["entries"]},
        )
        if resp.status_code == 201:
            print(f"已创建数据集 {name}（{len(data['entries'])} 条）")
        else:
            print(f"创建失败: {resp.status_code} {resp.text[:300]}")
            sys.exit(1)


if __name__ == "__main__":
    main()

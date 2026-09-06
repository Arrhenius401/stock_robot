"""配置雷达 API 的本地快照边界测试。"""

from fastapi.testclient import TestClient

from api.app import create_app


def test_radar_universes_are_available_without_upstream_request():
    client = TestClient(create_app(core=None, push=False))

    response = client.get("/api/v1/radar/universes")

    assert response.status_code == 200
    assert {item["id"] for item in response.json()} == {"cn_hk_etf", "overseas_etf"}


def test_radar_snapshot_requires_completed_local_snapshot():
    client = TestClient(create_app(core=None, push=False))

    response = client.get("/api/v1/radar/snapshots/latest", params={"universe_id": "cn_hk_etf"})

    assert response.status_code == 404

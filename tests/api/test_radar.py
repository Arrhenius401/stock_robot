"""配置雷达 API 的本地快照边界测试。"""

from typing import Literal

from fastapi.testclient import TestClient

from api.app import create_app
from radar.models import SnapshotItem
from radar.store import RadarStore
from utils.config import Config


def test_radar_universes_are_available_without_upstream_request():
    client = TestClient(create_app(core=None, push=False))

    response = client.get("/api/v1/radar/universes")

    assert response.status_code == 200
    assert {item["id"] for item in response.json()} == {"cn_hk_etf", "overseas_etf"}


def test_radar_snapshot_requires_completed_local_snapshot():
    client = TestClient(create_app(core=None, push=False))

    response = client.get("/api/v1/radar/snapshots/latest", params={"universe_id": "missing_universe"})

    assert response.status_code == 404


def test_radar_snapshot_exposes_its_own_status_summary_and_audit_metadata(tmp_path, monkeypatch):
    """历史快照不能错误复用后续刷新的状态汇总。"""
    config = Config(config_dir=tmp_path)
    monkeypatch.setattr("api.radar.Config", lambda: config)
    store = RadarStore(config.config_dir / "radar.db")
    run_id = store.create_run(
        universe_id="cn_hk_etf",
        universe_version=1,
        score_profile="core_etf_v1",
        provider="fallback_etf",
        as_of_date="2025-12-31",
    )
    items: tuple[tuple[str, Literal["fresh", "stale", "failed"]], ...] = (
        ("510500", "fresh"),
        ("510300", "stale"),
        ("159915", "failed"),
    )
    for symbol, status in items:
        store.add_item(run_id, SnapshotItem(
            symbol=symbol,
            name=symbol,
            category="cn_equity",
            status=status,
            data_source="TencentETFDataProvider" if status == "fresh" else None,
            grade="unavailable" if status == "failed" else "观察",
            error_summary="上游数据暂不可用" if status != "fresh" else None,
        ))
    store.complete_run(run_id)
    client = TestClient(create_app(core=None, push=False))

    response = client.get(f"/api/v1/radar/snapshots/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status_summary"] == {"fresh": 1, "stale": 1, "failed": 1}
    assert payload["provider"] == "fallback_etf"
    assert payload["completed_at"]
    fresh_item = next(item for item in payload["items"] if item["symbol"] == "510500")
    assert fresh_item["data_source"] == "TencentETFDataProvider"
    failed_run = store.create_run(
        universe_id="cn_hk_etf",
        universe_version=1,
        score_profile="core_etf_v1",
        provider="fallback_etf",
        as_of_date="2026-01-01",
    )
    store.fail_run(failed_run, "腾讯日线请求失败")

    latest = client.get("/api/v1/radar/snapshots/latest", params={"universe_id": "cn_hk_etf"})

    assert latest.status_code == 200
    assert latest.json()["last_refresh_failure"]["error_summary"] == "腾讯日线请求失败"


def test_radar_backtest_requires_existing_artifact():
    client = TestClient(create_app(core=None, push=False))

    response = client.get("/api/v1/radar/backtests/latest", params={"universe_id": "missing_universe"})

    assert response.status_code == 404


def test_radar_instrument_performance_rejects_missing_universe_without_upstream_request():
    client = TestClient(create_app(core=None, push=False))

    response = client.get(
        "/api/v1/radar/performance",
        params={"universe_id": "missing_universe", "symbol": "510500", "end_date": "2025-12-31"},
    )

    assert response.status_code == 404

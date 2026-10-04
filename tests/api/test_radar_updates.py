"""真实 Web 生命周期启动人工线程，不依赖独立采集心跳。"""

import time
from datetime import date

from fastapi.testclient import TestClient

from api.app import create_app
from radar.collector_store import CollectorStore
from radar.update_service import UpdateStore
from utils.config import Config


def test_web_lifecycle_executes_durable_manual_task_offline(tmp_path, monkeypatch):
    config = Config(config_dir=tmp_path)
    monkeypatch.setattr("api.radar.Config", lambda: config)
    calls = []

    def enqueue_manual(worker, now, universe_id, target):
        calls.append(universe_id)
        run = worker.store.enqueue(universe_id, date(2026, 9, 30), "cached", "manual", ["513500"])
        worker.store.finish(run["id"], "completed", snapshot_run_id="cached-snapshot")
        return [worker.store.get_run(run["id"])]

    monkeypatch.setattr("radar.collector_worker.CollectorWorker.enqueue_manual", enqueue_manual)
    monkeypatch.setattr("radar.overseas.OverseasService.refresh", lambda *_: {
        "items": [{"symbol": "513500", "status": "fresh", "as_of_date": "2026-10-02"}],
    })
    with TestClient(create_app(core=None, push=False)) as client:
        response = client.post("/api/v1/radar/refresh", json={"universe_id": "overseas_etf"})
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        for _ in range(100):
            result = client.get("/api/v1/radar/refresh/" + task_id).json()
            if result["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert result["status"] == "completed"
        assert result["overseas"]["items"][0]["as_of_date"] == "2026-10-02"
        assert calls == ["overseas_etf"]
        assert client.get("/api/v1/radar/collector/status").json()["service_online"] is False
    assert UpdateStore(tmp_path / "radar_collector.db").get(task_id)["status"] == "completed"
    assert CollectorStore(tmp_path / "radar_collector.db").runtime()["status"] == "never"


def test_missing_calendar_request_is_persisted_before_background_network(tmp_path, monkeypatch):
    config = Config(config_dir=tmp_path)
    monkeypatch.setattr("api.radar.Config", lambda: config)
    monkeypatch.setattr("radar.calendar.fetch_akshare_calendar", lambda: (_ for _ in ()).throw(AssertionError("请求线程不可联网")))
    client = TestClient(create_app(core=None, push=False))
    result = client.post("/api/v1/radar/refresh", json={"universe_id": "overseas_etf"})
    assert result.status_code == 202
    assert result.json()["status"] == "queued"
    assert client.get("/api/v1/radar/updates/latest", params={"universe_id": "overseas_etf"}).json()["id"] == result.json()["id"]

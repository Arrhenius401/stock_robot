"""配置雷达采集状态存储测试。"""

from radar.collector_store import CollectorStore


def test_collector_store_preserves_latest_status_per_universe(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")

    store.record("cn_hk_etf", "completed", "run-cn")
    store.record("overseas_etf", "failed", "上游超时")

    states = store.latest(("cn_hk_etf", "overseas_etf", "missing"))

    assert states[0]["status"] == "completed"
    assert states[0]["run_id"] == "run-cn"
    assert states[1]["status"] == "failed"
    assert states[1]["error_summary"] == "上游超时"
    assert states[2] == {"universe_id": "missing", "status": "never"}


def test_collector_store_records_runtime_and_recent_events(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")

    store.record_started()
    store.record_heartbeat()
    store.record("cn_hk_etf", "completed", "run-cn")

    assert store.runtime()["heartbeat_at"]
    assert store.recent_events()[0]["message"] == "cn_hk_etf 采集完成：run-cn"

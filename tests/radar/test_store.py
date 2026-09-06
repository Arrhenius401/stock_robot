"""本地不可变快照存储测试。"""

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import pytest

from radar.models import SnapshotItem
from radar.store import RadarStore


def _item(status: Literal["fresh", "stale", "failed"] = "fresh") -> SnapshotItem:
    return SnapshotItem(
        symbol="510300",
        name="沪深300ETF",
        category="a_share_large",
        status=status,
        observed_at=datetime.now().astimezone(),
        close=3.8,
        amount=1_000_000,
        score=80.0,
        rank=1,
        grade="偏好",
    )


def _create_run(store: RadarStore) -> str:
    return store.create_run(
        universe_id="cn_hk_etf",
        universe_version=1,
        score_profile="core_rotation_v1",
        provider="akshare_etf",
        as_of_date="2026-09-04",
    )


def test_running_run_is_invisible_and_completed_run_is_readable():
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        store = RadarStore(Path(tmp_dir) / "radar.db")
        run_id = _create_run(store)
        store.add_item(run_id, _item())

        assert store.latest_completed("cn_hk_etf") is None
        store.complete_run(run_id)

        snapshot = store.latest_completed("cn_hk_etf")
        assert snapshot is not None
        assert snapshot["run_id"] == run_id
        assert snapshot["items"][0]["symbol"] == "510300"


def test_completed_run_is_immutable_and_failed_run_keeps_old_snapshot():
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        store = RadarStore(Path(tmp_dir) / "radar.db")
        old_run = _create_run(store)
        store.add_item(old_run, _item())
        store.complete_run(old_run)
        failed_run = _create_run(store)
        store.fail_run(failed_run, "数据源失败")

        with pytest.raises(ValueError, match="已完成"):
            store.add_item(old_run, _item())
        latest = store.latest_completed("cn_hk_etf")
        assert latest is not None
        assert latest["run_id"] == old_run


def test_copy_healthy_item_and_status_summary():
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        store = RadarStore(Path(tmp_dir) / "radar.db")
        run_id = _create_run(store)
        store.add_item(run_id, _item())
        store.complete_run(run_id)

        old = store.copy_latest_healthy_item("cn_hk_etf", "510300")
        assert old is not None
        assert old.status == "fresh"
        assert old.source_run_id == run_id
        assert store.status_summary("cn_hk_etf") == {"fresh": 1, "stale": 0, "failed": 0}

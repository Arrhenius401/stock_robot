"""独立需求审查发现的日历恢复边界回归。"""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from radar.collector_store import CollectorStore
from radar.collector_worker import CollectorWorker
from radar.universe import UniverseRepository


def test_startup_calendar_recovers_on_holiday_still_schedules_latest(
    tmp_path, monkeypatch
):
    import radar.collector_worker as module

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 26, 9, tzinfo=ZoneInfo("Asia/Shanghai"))

    class Calendar:
        calls = 0

        def latest_completed(self, now):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("日历网络暂时失败")
            return date(2026, 9, 25)

        def trading_days(self, start, end):
            return (date(2026, 9, 25),) if start <= date(2026, 9, 25) <= end else ()

    class TwoPolls:
        count = 0

        def is_set(self):
            return self.count >= 2

        def wait(self, seconds):
            self.count += 1
            return False

    monkeypatch.setattr(module, "datetime", Clock)
    store = CollectorStore(tmp_path / "collector.db")
    store.update_settings(enabled=True, hour=18, minute=30, revision=store.settings()["revision"])
    repository = UniverseRepository(
        Path(__file__).parents[2] / "config/radar_universes"
    )
    service = CollectorWorker(
        store, repository, Calendar(), lambda *a, **k: "snapshot", state_dir=tmp_path
    )
    monkeypatch.setattr(service, "_stopping", TwoPolls())
    monkeypatch.setattr(service, "execute_one", lambda: False)
    service.serve()
    assert store.list_runs(), "启动补采不能因第一次日历失败而在休市日永久丢失"
    assert all(run["target_date"] == "2026-09-25" for run in store.list_runs())

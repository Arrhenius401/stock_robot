import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from push.models import Subscription
from push.store import (
    ActiveRunError,
    MissingSubscriptionError,
    PushStore,
    _process_alive,
)


def _sub(**kw: object):
    base: dict = {"name": "自选池", "symbols": ["600519", "000300"],
                  "channel": "email", "time": "08:00",
                  "created_at": "2026-08-20T08:00:00+08:00"}
    base.update(kw)
    return Subscription(**base)


def test_process_probe_does_not_signal_current_process(mocker):
    kill = mocker.patch("push.store.os.kill", side_effect=AssertionError("不得发送信号"))
    mocker.patch("push.store.sys.platform", "win32")
    kernel = mocker.Mock()
    kernel.OpenProcess.return_value = 123
    kernel.WaitForSingleObject.return_value = 258
    mocker.patch("ctypes.WinDLL", return_value=kernel, create=True)
    assert _process_alive(os.getpid()) is True
    kill.assert_not_called()
    kernel.CloseHandle.assert_called_once_with(123)


@pytest.mark.parametrize("handle,wait,error,expected", [(123, 0, 0, False), (0, 0, 87, False), (0, 0, 5, True)])
def test_windows_process_probe_exit_and_access_denied(mocker, handle, wait, error, expected):
    mocker.patch("push.store.sys.platform", "win32")
    kernel = mocker.Mock()
    kernel.OpenProcess.return_value = handle
    kernel.WaitForSingleObject.return_value = wait
    mocker.patch("ctypes.WinDLL", return_value=kernel, create=True)
    mocker.patch("ctypes.get_last_error", return_value=error, create=True)
    assert _process_alive(12345) is expected


class TestPushStore:
    def test_existing_database_schema_accepts_new_run(self, tmp_path):
        path = tmp_path / "push.db"
        with sqlite3.connect(path) as conn:
            conn.executescript("""
                CREATE TABLE subscriptions (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    symbols TEXT NOT NULL, channel TEXT NOT NULL, time TEXT NOT NULL,
                    enabled INTEGER NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE push_runs (id INTEGER PRIMARY KEY, subscription_id INTEGER NOT NULL,
                    ran_at REAL NOT NULL, total INTEGER NOT NULL, ok INTEGER NOT NULL,
                    failures TEXT NOT NULL);
            """)
        store = PushStore(path)
        sub_id = store.create(_sub())
        sub = store.get(sub_id)
        assert sub is not None
        run_id = store.queue_run(sub, "manual")
        run = store.get_run(run_id)
        assert run is not None
        assert run["status"] == "queued"

    def test_create_and_get(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        got = store.get(sub_id)
        assert got is not None
        assert got.name == "自选池"
        assert [s.symbol for s in got.symbols] == ["600519", "000300"]
        assert got.channel == "email"

    def test_create_and_get_roundtrip_kind(self, tmp_path):
        from push.models import SubscriptionSymbol
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub(symbols=[
            SubscriptionSymbol(symbol="000001", kind="index", index_style="broad")]))
        got = store.get(sub_id)
        assert got is not None
        assert got.symbols[0].kind == "index"
        assert got.symbols[0].index_style == "broad"

    def test_list_returns_all(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        store.create(_sub(name="a"))
        store.create(_sub(name="b"))
        assert [s.name for s in store.list()] == ["a", "b"]

    def test_update(self, tmp_path):
        from push.models import SubscriptionSymbol
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        sub = store.get(sub_id)
        assert sub is not None
        sub.enabled = False
        sub.symbols = [SubscriptionSymbol(symbol="000001", kind="stock")]
        assert store.update(sub) is True
        got = store.get(sub_id)
        assert got is not None
        assert got.enabled is False
        assert got.symbols[0].symbol == "000001"
        assert got.symbols[0].kind == "stock"

    def test_update_missing_returns_false(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        assert store.update(_sub(id=999)) is False

    def test_delete(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        assert store.delete(sub_id) is True
        assert store.get(sub_id) is None
        assert store.delete(sub_id) is False

    def test_record_and_list_runs(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        run_id = store.record_run(sub_id, total=2, ok=1, failures=["600519: 超时"])
        assert run_id > 0
        runs = store.list_runs(sub_id)
        assert len(runs) == 1
        assert runs[0]["ok"] == 1
        assert runs[0]["failures"] == ["600519: 超时"]

    def test_last_run_none_when_empty(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        assert store.last_run(sub_id) is None

    def test_active_run_is_unique_across_connections_and_tracks_progress(self, tmp_path):
        path = tmp_path / "push.db"
        first, second = PushStore(path), PushStore(path)
        sub_id = first.create(_sub())
        sub = first.get(sub_id)
        assert sub is not None
        run_id = first.queue_run(sub, "manual")
        with pytest.raises(ActiveRunError):
            second.queue_run(sub, "scheduled")
        first.start_run(run_id)
        first.set_current(run_id, "600519")
        run = second.get_run(run_id)
        assert run is not None
        assert run["current_symbol"] == "600519"
        first.advance_run(run_id, success=True)
        first.set_current(run_id, "000300")
        first.advance_run(run_id, success=False, failure="000300: 分析失败")
        run = first.finish_run(run_id)
        assert (run["status"], run["processed"], run["ok"]) == ("partial", 2, 1)
        assert run["failures"] == ["000300: 分析失败"]
        assert run["subscription_snapshot"]["name"] == "自选池"
        assert second.queue_run(sub, "manual") > run_id

    def test_recovery_only_changes_activity_and_delete_rejects_active(self, tmp_path, mocker):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        sub = store.get(sub_id)
        assert sub is not None
        run_id = store.queue_run(sub, "manual")
        with pytest.raises(ActiveRunError):
            store.delete(sub_id)
        assert PushStore(tmp_path / "push.db").recover_interrupted() == 0  # 新实例不能打断活跃 CLI
        mocker.patch("push.store._process_alive", return_value=False)
        assert store.recover_interrupted() == 1
        run = store.get_run(run_id)
        assert run is not None
        assert run["status"] == "interrupted"
        assert store.recover_interrupted() == 0
        assert store.delete(sub_id)

    def test_deleted_subscription_cannot_be_queued_from_stale_object(self, tmp_path):
        first, second = PushStore(tmp_path / "push.db"), PushStore(tmp_path / "push.db")
        sub_id = first.create(_sub())
        stale = first.get(sub_id)
        assert stale is not None
        assert second.delete(sub_id)
        with pytest.raises(MissingSubscriptionError):
            first.queue_run(stale, "manual")
        assert first.list_runs(sub_id) == []

    def test_delete_and_queue_race_never_leaves_orphan_run(self, tmp_path):
        first, second = PushStore(tmp_path / "push.db"), PushStore(tmp_path / "push.db")
        sub_id = first.create(_sub())
        sub = first.get(sub_id)
        assert sub is not None
        barrier = threading.Barrier(2)

        def queue():
            barrier.wait()
            try:
                return first.queue_run(sub, "manual")
            except MissingSubscriptionError:
                return None

        def delete():
            barrier.wait()
            try:
                return second.delete(sub_id)
            except ActiveRunError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            run_future = pool.submit(queue)
            delete_future = pool.submit(delete)
            run_id, deleted = run_future.result(), delete_future.result()
        assert (run_id is None and deleted) or (run_id is not None and not deleted)
        assert (first.get_run(run_id) is not None) if run_id is not None else first.list_runs(sub_id) == []

    def test_legacy_wecom_row_is_hidden_without_deleting_it(self, tmp_path):
        path = tmp_path / "push.db"
        store = PushStore(path)
        with sqlite3.connect(path) as conn:
            conn.execute("INSERT INTO subscriptions (id,name,symbols,channel,time,enabled,created_at)"
                         " VALUES (9,'legacy','[]','wecom','08:00',1,'old')")
        assert store.list() == []
        assert store.get(9) is None
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT channel FROM subscriptions WHERE id=9").fetchone()[0] == "wecom"

    def test_finish_error_forces_failed_even_after_progress(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        sub = store.get(sub_id)
        assert sub is not None
        run_id = store.queue_run(sub, "manual")
        store.start_run(run_id)
        store.advance_run(run_id, success=True)
        run = store.finish_run(run_id, error="执行器中断")
        assert run["status"] == "failed"
        assert run["ok"] == 1
        assert run["failures"] == ["执行器中断"]

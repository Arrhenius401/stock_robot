"""维护任务：状态、锁与后台循环。"""
import time
from datetime import UTC, datetime

import pytest
from filelock import FileLock

from maintenance.runner import MaintenanceRunner


class ConfigStub(dict):
    """只实现维护模块用到的嵌套读取。"""

    def get(self, key, default=None):
        node = self
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node


def make_config(**overrides):
    maintenance = {
        "report_cleanup_enabled": True,
        "sweep_interval_minutes": 360,
    }
    maintenance.update(overrides)
    return ConfigStub({"maintenance": maintenance})


def make_report(root, when):
    month = root / "stock" / "600519" / when.strftime("%Y-%m")
    month.mkdir(parents=True, exist_ok=True)
    path = month / f"600519_{when.strftime('%Y%m%d_%H%M%S')}.md"
    path.write_text("# 报告\n", encoding="utf-8")
    return path


@pytest.fixture
def runner_factory(tmp_path):
    roots = []

    def factory(config):
        root = tmp_path / f"reports-{len(roots)}"
        root.mkdir()
        roots.append(root)
        state = tmp_path / f"state-{len(roots)}"
        state.mkdir()
        runner = MaintenanceRunner(
            root, state_dir=state, config_factory=lambda: config
        )
        return runner, root

    return factory


def test_disabled_config_reports_state_without_deleting(runner_factory, tmp_path):
    runner, root = runner_factory(make_config(report_cleanup_enabled=False))
    path = make_report(root, datetime(2026, 10, 1, 9, 0, tzinfo=UTC))

    status = runner.run_once()

    assert status["state"] == "disabled"
    assert path.exists()
    assert runner.status()["last_run"]["state"] == "disabled"
    assert runner.status()["config"]["report_cleanup_enabled"] is False


def test_expired_reports_are_deleted_and_recorded(runner_factory):
    runner, root = runner_factory(make_config())
    path = make_report(root, datetime(2026, 10, 1, 9, 0, tzinfo=UTC))

    status = runner.run_once()

    assert status["state"] == "ok"
    assert status["deleted"] == 1
    assert not path.exists()
    payload = runner.status()
    assert payload["config"] == {
        "report_cleanup_enabled": True,
        "sweep_interval_minutes": 360,
    }
    assert payload["last_run"]["deleted"] == 1


def test_lock_held_by_another_instance_skips_this_round(runner_factory, tmp_path):
    runner, root = runner_factory(make_config())
    path = make_report(root, datetime(2026, 10, 1, 9, 0, tzinfo=UTC))
    state = next(tmp_path.glob("state-*"))

    with FileLock(str(state / "maintenance.lock"), timeout=0):
        status = runner.run_once()

    assert status["state"] == "locked"
    assert path.exists()


def test_legacy_retention_days_setting_is_ignored(runner_factory):
    """旧配置里若残留保留天数，仍按固定 7 天执行。"""
    runner, root = runner_factory(make_config(report_retention_days=3650))
    path = make_report(root, datetime(2026, 10, 21, 9, 0, tzinfo=UTC))

    status = runner.run_once()

    assert status["deleted"] == 1
    assert not path.exists()


def test_background_thread_runs_once_and_stops(runner_factory):
    runner, _ = runner_factory(make_config())
    runner.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and runner.status()["last_run"] is None:
        time.sleep(0.02)

    runner.stop()

    assert runner.status()["last_run"] is not None
    assert not runner.running

"""维护任务：按配置周期性清理报告库。"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from maintenance.report_retention import sweep
from utils.config import Config

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_MINUTES = 360
MIN_INTERVAL_MINUTES = 1


class MaintenanceRunner:
    """持有一个后台线程；跨进程锁保证同时只有一个实例在删除。"""

    def __init__(
        self,
        reports_root: Path,
        *,
        state_dir: Path,
        config_factory: Callable[[], Any] = Config,
        interval_minutes: int | None = None,
    ) -> None:
        self._reports_root = Path(reports_root)
        self._state_dir = Path(state_dir)
        self._config_factory = config_factory
        self._interval_override = interval_minutes
        self._stop = threading.Event()
        self._guard = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_run: dict[str, Any] | None = None

    def start(self) -> None:
        with self._guard:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="stock-robot-maintenance",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self._guard:
            thread = self._thread
            self._thread = None
        if thread is not None:
            thread.join(timeout)

    @property
    def running(self) -> bool:
        with self._guard:
            thread = self._thread
        return thread is not None and thread.is_alive()

    def status(self) -> dict[str, Any]:
        config = self._config_factory()
        with self._guard:
            last_run = dict(self._last_run) if self._last_run is not None else None
        return {
            "config": {
                "report_cleanup_enabled": bool(
                    config.get("maintenance.report_cleanup_enabled", True)
                ),
                "sweep_interval_minutes": self._interval_minutes(config),
            },
            "running": self.running,
            "last_run": last_run,
        }

    def run_once(self) -> dict[str, Any]:
        """执行一轮清理；返回本次结果并记录为最近一次运行。"""
        config = self._config_factory()
        enabled = bool(config.get("maintenance.report_cleanup_enabled", True))
        status: dict[str, Any] = {
            "at": time.time(),
            "state": "ok" if enabled else "disabled",
            "scanned": 0,
            "deleted": 0,
            "orphans": 0,
            "freed_bytes": 0,
            "skipped": 0,
            "failures": 0,
        }
        if enabled:
            lock = FileLock(str(self._state_dir / "maintenance.lock"), timeout=0)
            try:
                with lock:
                    status.update(
                        sweep(self._reports_root, enabled=True).summary()
                    )
            except Timeout:
                status["state"] = "locked"
        self._record(status)
        return status

    def _loop(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 — 后台线程不得因单轮失败退出
                logger.warning("维护任务执行失败：%s", type(exc).__name__)
            if self._stop.wait(self._next_interval()):
                return

    def _next_interval(self) -> float:
        try:
            return self._interval_minutes(self._config_factory()) * 60
        except Exception as exc:  # noqa: BLE001 — 配置损坏时退回默认间隔继续重试
            logger.warning("维护任务读取间隔失败，使用默认值：%s", type(exc).__name__)
            return DEFAULT_INTERVAL_MINUTES * 60

    def _interval_minutes(self, config: Any) -> int:
        if self._interval_override is not None:
            return max(MIN_INTERVAL_MINUTES, int(self._interval_override))
        return max(
            MIN_INTERVAL_MINUTES,
            _positive_int(
                config.get("maintenance.sweep_interval_minutes", DEFAULT_INTERVAL_MINUTES),
                DEFAULT_INTERVAL_MINUTES,
            ),
        )

    def _record(self, status: dict[str, Any]) -> None:
        with self._guard:
            self._last_run = status


def _positive_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value >= 1 else default
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed >= 1 else default
    return default

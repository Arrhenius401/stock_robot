"""配置雷达的定时采集守护服务。"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class RadarCollector:
    """在工作日收盘后依次刷新独立 ETF 池。"""

    def __init__(
        self,
        refresh: Callable[[str], str],
        universe_ids: tuple[str, ...],
        record: Callable[[str, str, str | None], None] | None = None,
        record_started: Callable[[], None] | None = None,
        record_heartbeat: Callable[[], None] | None = None,
    ):
        self._refresh = refresh
        self._universe_ids = universe_ids
        self._record = record
        self._record_started_callback = record_started
        self._record_heartbeat_callback = record_heartbeat

    def run_once(self) -> dict[str, str]:
        """执行一次采集；单池失败不阻断其他池。"""
        results: dict[str, str] = {}
        for universe_id in self._universe_ids:
            try:
                run_id = self._refresh(universe_id)
                results[universe_id] = run_id
                self._record_result(universe_id, "completed", run_id)
            except (RuntimeError, ValueError) as exc:
                logger.warning("雷达采集失败: %s: %s", universe_id, exc)
                results[universe_id] = f"failed: {exc}"
                self._record_result(universe_id, "failed", str(exc))
        return results

    def _record_result(self, universe_id: str, status: str, result: str) -> None:
        """状态审计写入失败不影响独立池的采集结果。"""
        if self._record is None:
            return
        try:
            self._record(universe_id, status, result)
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.warning("雷达采集状态未写入: %s: %s", universe_id, exc)

    def serve(self, *, hour: int, minute: int) -> None:
        """前台运行工作日定时采集。"""
        scheduler = BlockingScheduler(timezone="Asia/Shanghai")
        self._record_runtime(self._record_started_callback)
        scheduler.add_job(
            self.run_once,
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
            id="radar-collector",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            lambda: self._record_runtime(self._record_heartbeat_callback),
            "interval",
            minutes=5,
            id="radar-collector-heartbeat",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()

    @staticmethod
    def _record_runtime(callback: Callable[[], None] | None) -> None:
        """运行审计异常不应阻断定时采集。"""
        if callback is None:
            return
        try:
            callback()
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.warning("雷达采集运行状态未写入: %s", exc)

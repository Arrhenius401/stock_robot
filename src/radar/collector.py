"""配置雷达的定时采集守护服务。"""

from __future__ import annotations

import logging
from collections.abc import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class RadarCollector:
    """在工作日收盘后依次刷新独立 ETF 池。"""

    def __init__(self, refresh: Callable[[str], str], universe_ids: tuple[str, ...]):
        self._refresh = refresh
        self._universe_ids = universe_ids

    def run_once(self) -> dict[str, str]:
        """执行一次采集；单池失败不阻断其他池。"""
        results: dict[str, str] = {}
        for universe_id in self._universe_ids:
            try:
                results[universe_id] = self._refresh(universe_id)
            except (RuntimeError, ValueError) as exc:
                logger.warning("雷达采集失败: %s: %s", universe_id, exc)
                results[universe_id] = f"failed: {exc}"
        return results

    def serve(self, *, hour: int, minute: int) -> None:
        """前台运行工作日定时采集。"""
        scheduler = BlockingScheduler(timezone="Asia/Shanghai")
        scheduler.add_job(
            self.run_once,
            CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute),
            id="radar-collector",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()

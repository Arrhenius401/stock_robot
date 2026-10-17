"""仅执行手动队列的股票后台线程，不注册自动计划。"""
from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from radar.calendar import CalendarUnavailable, TradingCalendar
from radar.stocks.market_context import prepare_market_window
from radar.stocks.update_runner import StockUpdateRunner

logger = logging.getLogger(__name__)


def load_market_window(state_dir: Path, as_of: date) -> tuple[list[date] | None, Any]:
    """缺少缓存时明确未知，不联网补日历。"""
    calendar = TradingCalendar(state_dir / 'radar_collector.db', refresh_enabled=False)
    try:
        trace = prepare_market_window(calendar, as_of, now=datetime.now().astimezone())
    except CalendarUnavailable as exc:
        return None, {'status': 'unavailable', 'reason': str(exc)}
    return [date.fromisoformat(day) for day in trace['days']], trace


class StockUpdateService:
    """复用runner的租约与心跳；停止后不再领取下一项任务。"""

    def __init__(self, runner: StockUpdateRunner):
        self.runner = runner
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.worker_id = 'stock-web-' + uuid.uuid4().hex

    def wake(self) -> None:
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError('股票任务服务正在关闭')
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name=self.worker_id, daemon=True)
                self._thread.start()
            self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=15)
            self._wake.clear()
            while not self._stop.is_set():
                try:
                    task = self.runner.execute_one(self.worker_id)
                except (OSError, ValueError, sqlite3.Error):
                    logger.exception('股票手动任务服务执行失败')
                    break
                if task is None:
                    break

    def stop(self, *, timeout: float = 5) -> None:
        """采集中保留runner心跳，适配器返回后收敛，不释放给旧执行者的租约。"""
        with self._lock:
            self._stop.set()
            self._wake.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

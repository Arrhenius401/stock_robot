"""独立采集协调器：持久队列、跨进程互斥与安全边界取消。"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from filelock import FileLock, Timeout

from radar.collector_store import CollectorStore
from radar.score_profile import ScoreProfileConfigError, ScoreProfileRepository
from radar.universe import UniverseConfigError, UniverseRepository

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo('Asia/Shanghai')


class CollectorWorker:
    """网页只入队，常驻服务认领并执行；自动关闭时仍保持待命。"""

    def __init__(self, store: CollectorStore, repository: UniverseRepository, calendar: Any,
                 refresh: Callable[..., str], *, state_dir: Path):
        self.store = store
        self.repository = repository
        self.calendar = calendar
        self.refresh = refresh
        self.state_dir = state_dir.resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.worker_id = uuid.uuid4().hex
        self._stopping = threading.Event()
        self._daemon_lock = FileLock(self.state_dir / 'radar_collector.service.lock')
        self._execute_lock = FileLock(self.state_dir / 'radar_collector.execute.lock')

    def _fingerprint(self, universe: Any, target: date) -> str:
        profile = ScoreProfileRepository(self.repository.directory.parent / 'radar_score_profiles').get(universe.score_profile)
        payload = {'universe': universe.model_dump(mode='json'), 'profile': profile.model_dump(mode='json'), 'target': str(target)}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def _enqueue(self, now: datetime, source: str, universe_id: str | None = None, target_date: date | None = None) -> list[dict[str, Any]]:
        latest = self.calendar.latest_completed(now)
        target = target_date or latest
        if target > latest or not self.calendar.trading_days(target, target):
            raise ValueError("目标日期必须是已结束交易日")
        if source != "manual":
            self.store.cancel_superseded(target)
        pools = self.repository.enabled_on(target)
        if universe_id is not None:
            pools = [pool for pool in pools if pool.id == universe_id]
            if not pools:
                raise ValueError('标的池未启用或在目标日期未生效')
        fingerprints = {pool.id: self._fingerprint(pool, target) for pool in pools}
        return [self.store.enqueue(pool.id, target, fingerprints[pool.id], source,
                                   [item.symbol for item in pool.instruments], universe_version=pool.version)
                for pool in pools]

    def enqueue_manual(self, now: datetime, universe_id: str | None = None, target_date: date | None = None) -> list[dict[str, Any]]:
        """手动也使用已闭市目标日及同一幂等键。"""
        runs = self._enqueue(now, 'manual', universe_id, target_date)
        return [self.store.retry(run['id'], manual=True) if run['status'] in {'cancelled','partial','failed'} else self.store.promote_manual(run['id']) for run in runs]

    def coordinate(self, now: datetime, *, recovery: bool = False) -> list[dict[str, Any]]:
        """启动/配置变化补最近目标，其余轮询遵守交易日设置时间。"""
        settings = self.store.settings()
        if not settings['enabled']:
            return []
        local = now.astimezone(SHANGHAI)
        if not recovery:
            if (local.hour, local.minute) < (settings['hour'], settings['minute']):
                return []
            if not self.calendar.trading_days(local.date(), local.date()):
                return []
        runs = self._enqueue(local, 'recovery' if recovery else 'automatic')
        return [self.store.retry(run['id'], manual=False) if run['status'] == 'cancelled' else run for run in runs]

    def execute_one(self, now: datetime | None = None) -> bool:
        """先取执行锁，再认领任务，防止不同进程同时发起数据请求。"""
        try:
            with self._execute_lock.acquire(timeout=0):
                run = self.store.claim(self.worker_id, now, lease_seconds=90)
                if run is None:
                    return False
                self._execute(run)
                return True
        except Timeout:
            return False

    def _execute(self, run: dict[str, Any]) -> None:
        # 心跳续租独立于阻塞的数据请求，进程崩溃后才会过期回收。
        done = threading.Event()

        def heartbeat() -> None:
            while not done.wait(15):
                try:
                    current = self.store.get_run(run['id'])
                    if current is None or current['status'] != 'running':
                        return
                    self.store.renew_lease(run['id'], self.worker_id, lease_seconds=90)
                    self.store.record_heartbeat(worker_id=self.worker_id, phase=current['phase'])
                except (ValueError, sqlite3.Error) as exc:
                    logger.warning('采集心跳续租失败：%s', exc)
                    return

        pulse = threading.Thread(target=heartbeat, daemon=True)
        pulse.start()
        run_id = run['id']

        def cancelled() -> bool:
            current = self.store.get_run(run_id)
            if current is None or current['cancel_requested'] or self._stopping.is_set():
                return True
            if current['source'] != 'manual' and not self.store.settings()['enabled']:
                return True
            pools = self.repository.enabled_on(date.fromisoformat(run['target_date']))
            matching = next((pool for pool in pools if pool.id == run['universe_id']), None)
            return matching is None or self._fingerprint(matching, date.fromisoformat(run['target_date'])) != run['fingerprint']

        def phase(value: str) -> None:
            self.store.set_phase(run_id, 'syncing' if value == 'preparing' else value, worker_id=self.worker_id)

        def item(symbol: str, success: bool, error: str | None) -> None:
            current = self.store.get_run(run_id)
            previous = next((entry for entry in current['items'] if entry['symbol'] == symbol), None) if current else None
            if not success and previous and previous['status'] == 'completed':
                self.store.invalidate_item(run_id, symbol, error or '已完成行情缓存校验失败', self.worker_id)
            else:
                self.store.record_item(run_id, symbol, 'completed' if success else 'failed', error, worker_id=self.worker_id)

        try:
            if cancelled():
                self.store.finish(run_id, 'cancelled', '采集已关闭或池配置已变更', worker_id=self.worker_id)
                return
            target = date.fromisoformat(run['target_date'])
            cached_calendar = self.calendar.cached_data() if hasattr(self.calendar, "cached_data") else None
            first_day = cached_calendar.coverage_start if cached_calendar is not None else target - timedelta(days=500)
            days = self.calendar.trading_days(first_day, target)
            symbols = {entry['symbol'] for entry in run['items'] if entry['status'] != 'completed'}
            snapshot = self.refresh(run['universe_id'], as_of=target, retry_symbols=symbols,
                                    on_phase=phase, on_item=item, should_cancel=cancelled, trading_days=days)
            latest = self.store.get_run(run_id)
            assert latest is not None
            failed = [entry for entry in latest['items'] if entry['status'] != 'completed']
            status = 'partial' if failed else 'completed'
            self.store.finish(run_id, status, '部分标的失败' if failed else None,
                              snapshot_run_id=snapshot, worker_id=self.worker_id)
            if failed:
                self._retry(run_id)
        except Exception as exc:  # noqa: BLE001, RUF100 — 单任务隔离，记录失败后继续服务
            logger.exception('采集任务 %s 失败', run_id)
            current = self.store.get_run(run_id)
            if current is None or current['status'] != 'running' or current['worker_id'] != self.worker_id:
                logger.warning('任务已收敛或租约已移交，原执行者不再改写：%s', run_id)
                return
            if type(exc).__name__ == 'RadarRefreshCancelled' or current['cancel_requested'] or self._stopping.is_set():
                self.store.finish(run_id, 'cancelled', str(exc), worker_id=self.worker_id)
            else:
                self.store.finish(run_id, 'failed', str(exc), worker_id=self.worker_id)
                # 配置/验证错误不重复请求；数据源/网络/隔离执行错误可有限重试。
                if not isinstance(exc, (ValueError, UniverseConfigError, ScoreProfileConfigError)):
                    self._retry(run_id)
        finally:
            done.set()
            pulse.join(timeout=1)

    def _retry(self, run_id: str) -> None:
        current = self.store.get_run(run_id)
        if current is not None and current['attempt'] < 3:
            if current['source'] != 'manual' and not self.store.settings()['enabled']:
                return
            try:
                self.store.retry(run_id, manual=False)
            except ValueError as exc:
                logger.info('任务不再满足重试条件：%s', exc)

    def stop(self) -> None:
        """信号处理仅请求停止，下一个标的安全边界退出。"""
        self._stopping.set()

    def serve(self, *, startup_hook: Callable[[], None] | None = None) -> None:
        """单实例服务持续待命；网页重启不影响队列。"""
        with self._daemon_lock.acquire(timeout=0):
            if startup_hook is not None:
                startup_hook()
            self.store.record_started(worker_id=self.worker_id)
            previous_schedule: tuple[bool, int, int] | None = None
            previous_poll: datetime | None = None
            previous_error: str | None = None
            while not self._stopping.is_set():
                now = datetime.now(SHANGHAI)
                self.store.recover_expired()
                settings = self.store.settings()
                schedule = (settings['enabled'], settings['hour'], settings['minute'])
                try:
                    # 关闭自动时也预热日历，网页手动请求只读此缓存。
                    self.calendar.latest_completed(now)
                    resumed = previous_poll is None or (now - previous_poll).total_seconds() > 60 or now < previous_poll
                    # 文件版本负责并发保存；只有采集计划变化才触发补采。
                    self.coordinate(now, recovery=resumed or previous_error is not None or previous_schedule != schedule)
                    previous_error = None
                    self.store.record_heartbeat(worker_id=self.worker_id)
                except (ValueError, RuntimeError, UniverseConfigError, ScoreProfileConfigError, OSError) as exc:
                    message = str(exc)
                    if message != previous_error:
                        logger.error('自动采集计划不可执行：%s', exc)
                        self.store.record_event('error', message)
                    previous_error = message
                    self.store.record_heartbeat(worker_id=self.worker_id, phase='blocked', error_summary=message)
                previous_schedule = schedule
                previous_poll = now
                self.execute_one()
                self._stopping.wait(5)

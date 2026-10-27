"""黄金独立缓存和有限并发采集；不读写配置雷达业务数据。"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as day_time
from pathlib import Path
from typing import Any, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from gold.models import GoldCollection, GoldRules, GoldSeries, SourceKey, local_now
from gold.sources import SOURCE_INFO, SourceParseError, empty_series
from radar.provider_request import bounded_provider_call

logger = logging.getLogger(__name__)
_REQUEST_SLOTS = threading.BoundedSemaphore(3)


@dataclass(frozen=True)
class SourceCalendar:
    """仅接受已核验的来源日历；未知日历应返回 None。"""

    closed: bool
    next_update: datetime | None = None
    evidence: str = ''


def source_calendar(key: SourceKey, now: datetime) -> SourceCalendar | None:
    """只覆盖官方常规周末/发布窗口；不猜测法定假期和提供商交易日历。"""
    if key == 'domestic':
        local = now.astimezone(ZoneInfo('Asia/Shanghai'))
        if (local.weekday() == 5 and local.time() >= day_time(2, 30)) or local.weekday() == 6:
            monday = (local + timedelta(days=7-local.weekday())).replace(hour=9, minute=0, second=0, microsecond=0)
            return SourceCalendar(True, monday, 'https://en.sge.com.cn/h5_trading_ProductsIntroduce；周六02:30至周一09:00休市；未知节假日不延长')
    if key in ('dollar', 'fx'):
        local = now.astimezone(ZoneInfo('America/New_York'))
        monday = (local + timedelta(days=(-local.weekday()) % 7)).replace(hour=16, minute=15, second=0, microsecond=0)
        # 周一假日可能延至周二；两日发布时段不据常规周历延长缓存。
        if local.weekday() == 0 and local < monday:
            return SourceCalendar(True, monday, 'https://www.federalreserve.gov/releases/h10/；常规周一16:15纽约发布，节假日顺延窗口保守6小时')
        if local.weekday() >= 2:
            return SourceCalendar(True, monday, 'https://www.federalreserve.gov/releases/h10/；每周发布；周三至周日常规非发布窗，周一/二假日顺延保守')
    if key == 'real_yield':
        local = now.astimezone(ZoneInfo('America/New_York'))
        if local.weekday() >= 5:
            monday = (local + timedelta(days=7-local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            return SourceCalendar(True, monday, 'https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics；business day日频，周末延长不跨周一零点')
    return None


class GoldCollector:
    def __init__(
        self, config_dir: Path, rules: GoldRules | None = None, *,
        request_executor: Callable[..., Any] = bounded_provider_call,
        sources: Sequence[SourceKey] | None = None,
        calendars: Mapping[str, Callable[[datetime], SourceCalendar | None]] | None = None,
    ) -> None:
        self.rules = rules or GoldRules()
        self.directory = Path(config_dir) / 'gold'
        self.request_executor = request_executor
        self.sources: list[SourceKey] = list(sources) if sources is not None else [cast(SourceKey, key) for key in SOURCE_INFO]
        if len(set(self.sources)) != len(self.sources) or any(key not in SOURCE_INFO for key in self.sources):
            raise ValueError('黄金来源必须为已知且不重复的系列')
        self.calendars: Mapping[str, Callable[[datetime], SourceCalendar | None]] = calendars or {}

    def _cache_path(self, key: SourceKey) -> Path:
        info = SOURCE_INFO[key]
        identity = json.dumps([key, info[1], info[3], 'monthly' if 'reserves' in key else 'daily',
                               self.rules.version, 'source-schema-completed-reserve-v1'], ensure_ascii=False)
        digest = hashlib.sha256(identity.encode()).hexdigest()[:16]
        return self.directory / 'cache' / f'{key}-{digest}.json'

    def _read_cache(self, key: SourceKey) -> tuple[GoldSeries, datetime] | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            series = GoldSeries.model_validate(payload['series'])
            expiry = datetime.fromisoformat(payload['expires_at'])
            if series.key != key or series.unit != SOURCE_INFO[key][3] or expiry.tzinfo is None:
                raise ValueError('缓存系列、单位或时区不符合当前来源')
            return series, expiry
        except (OSError, KeyError, TypeError, ValueError) as exc:
            logger.warning('黄金缓存损坏，重新请求 %s: %s', key, exc)
            return None

    def cache_expiry(self, key: SourceKey, now: datetime) -> datetime:
        if 'reserves' in key:
            return now + timedelta(seconds=self.rules.monthly_cache_seconds)
        seconds = self.rules.daily_cache_seconds
        calendar_reader = self.calendars.get(key)
        calendar_info = calendar_reader(now) if calendar_reader else source_calendar(key, now)
        if calendar_info is not None and calendar_info.evidence:
            if calendar_info.closed:
                seconds = min(self.rules.closed_cache_seconds, 86400)
            next_update = calendar_info.next_update
            if next_update is not None:
                if next_update.tzinfo is None:
                    raise ValueError('来源下一更新时点必须含时区')
                seconds = min(seconds, max(0, (next_update - now).total_seconds()))
        return now + timedelta(seconds=seconds)

    def _write_cache(self, key: SourceKey, series: GoldSeries, now: datetime) -> None:
        path = self._cache_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f'.{uuid4().hex}.tmp')
        try:
            payload = {'expires_at': self.cache_expiry(key, now).isoformat(), 'series': series.model_dump(mode='json')}
            temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _request(self, key: SourceKey, now: datetime, deadline: float) -> GoldSeries:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('黄金采集总体预算已截止，未启动此来源')
        if not _REQUEST_SLOTS.acquire(timeout=remaining):
            raise TimeoutError('等待黄金共享请求槽位时已到总体截止')
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('黄金采集总体预算已截止，未启动此来源')
            timeout = min(self.rules.source_timeout_seconds, 30, remaining)
            result = self.request_executor(
                'gold.sources', 'fetch_source',
                {'key': key, 'now_iso': now.isoformat(), 'history_dir': str(self.directory / 'history'),
                 'historical_cache_seconds': self.rules.historical_cache_seconds}, timeout=timeout,
            )
        finally:
            _REQUEST_SLOTS.release()
        series = GoldSeries.model_validate(result)
        if series.key != key or series.unit != SOURCE_INFO[key][3]:
            raise SourceParseError('采集结果系列或单位与请求不一致')
        if any(point.observation_date > now.date() for point in series.observations) or any(bar.trade_date > now.date() for bar in series.bars):
            raise SourceParseError('采集结果包含未来日期')
        if series.status != 'ok' or not (series.observations or series.bars):
            raise SourceParseError(series.error or '来源未返回有效数据')
        return series

    def collect(self, *, now: datetime | None = None, refresh: bool = False) -> GoldCollection:
        now = now or local_now()
        if now.tzinfo is None:
            raise ValueError('黄金采集时间必须含时区')
        deadline = time.monotonic() + min(self.rules.total_timeout_seconds, 60)
        result = GoldCollection(generated_at=now)
        old: dict[SourceKey, GoldSeries] = {}
        pending: list[SourceKey] = []
        for key in self.sources:
            if key == 'global_reserves':
                result.series[key] = empty_series(key, now, 'IMF 全球数量序列尚未通过接入验收；不使用美元估值替代数量')
                continue
            cached = self._read_cache(key)
            if cached:
                series, expiry = cached
                old[key] = series
                if not refresh and expiry > now and series.fetched_at <= now:
                    result.series[key] = series
                    continue
            pending.append(key)

        def fail(key: SourceKey, reason: str) -> None:
            if key in old:
                previous = old[key].model_copy(deep=True)
                previous.status, previous.error = 'stale', reason
                previous.notes.append('本次更新失败，保留原观察日期和原采集时间的旧值')
                result.series[key] = previous
            else:
                result.series[key] = empty_series(key, now, reason)

        # 调度线程仅等待可终止请求；每次请求超时取剩余总预算，退出时必须等所有边界清理。
        with ThreadPoolExecutor(max_workers=self.rules.max_workers, thread_name_prefix='gold-source') as pool:
            running: dict[Future[GoldSeries], SourceKey] = {}
            while pending or running:
                while pending and len(running) < self.rules.max_workers and time.monotonic() < deadline:
                    key = pending.pop(0)
                    running[pool.submit(self._request, key, now, deadline)] = key
                if not running:
                    break
                completed, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in completed:
                    key = running.pop(future)
                    try:
                        series = future.result()
                    except Exception as exc:  # noqa: BLE001 — 单来源隔离边界，不影响其他黄金证据
                        logger.warning('黄金来源 %s 请求失败: %s', key, exc)
                        fail(key, f'{type(exc).__name__}: {exc}')
                        continue
                    result.series[key] = series
                    calendar_info = self.calendars[key](now) if key in self.calendars else source_calendar(key, now)
                    if calendar_info is not None and calendar_info.evidence:
                        series.metadata['cache_calendar_evidence'] = calendar_info.evidence
                        series.metadata['cache_calendar_timezone'] = str(calendar_info.next_update.tzinfo) if calendar_info.next_update else None
                    try:
                        self._write_cache(key, series, now)
                    except (OSError, ValueError) as exc:
                        logger.warning('黄金来源已取得但缓存保存失败 %s: %s', key, exc)
                        series.notes.append(f'缓存保存失败: {type(exc).__name__}')
            for key in pending:
                fail(key, '黄金采集总体预算已截止，未启动此来源')
        if any(series.status != 'ok' for series in result.series.values()):
            result.notes.append('部分来源缺失或更新失败；旧值保留真实观察日期，不按抓取时间刷新数据期')
        return result

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

from filelock import FileLock, Timeout

from data.base import CollectionResult
from data.collection_state import make_record, record_consistent
from data.schemas import DataCollectionRecord
from gold.models import GoldCollection, GoldRules, GoldSeries, SourceKey, local_now
from gold.sources import SOURCE_INFO, empty_series
from radar.provider_request import bounded_provider_call

logger = logging.getLogger(__name__)
_REQUEST_SLOTS = threading.BoundedSemaphore(3)
_RETRY_SECONDS = 1800


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

    def _snapshot_path(self, key: SourceKey, kind: str) -> Path:
        path = self._cache_path(key)
        return path if kind == "latest" else path.with_name(f"{path.stem}-{kind}.json")

    def _read_cache(self, key: SourceKey, now: datetime, kind: str = "latest") -> tuple[GoldSeries, datetime] | None:
        from gold.policy import classify_gold

        path = self._snapshot_path(key, kind)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            series = GoldSeries.model_validate(payload["series"])
            expiry = datetime.fromisoformat(payload["expires_at"])
            stored_at = datetime.fromisoformat(payload["stored_at"]) if "stored_at" in payload else series.fetched_at
            if (series.key != key or series.unit != SOURCE_INFO[key][3] or expiry.tzinfo is None
                    or series.fetched_at > now or stored_at.utcoffset() is None or stored_at > now
                    or series.status != "ok"):
                raise ValueError("缓存系列、单位、时间或状态无效")
            current_state = classify_gold(series, None, now, self.rules)
            if current_state == "failed":
                raise ValueError("缓存黄金数据未通过有效性校验")
            record = series.collection_record
            if record is None or record.result_state == "unknown":
                record = make_record(series.source, current_state, collected_at=series.fetched_at)
            elif not record_consistent(record) or record.result_state not in ("complete", "partial"):
                raise ValueError("缓存采集状态不一致")
            elif record.result_state == "complete" and current_state == "partial":
                record = make_record(series.source, "partial", diagnostic=record,
                                     collected_at=series.fetched_at, cache_status=record.cache_status)
            if (record.collected_at is None or record.collected_at.utcoffset() is None
                    or record.collected_at != series.fetched_at or record.source != series.source):
                raise ValueError("缓存采集时间无效")
            series.collection_record = record
            if record.result_state == "partial":
                expiry = min(expiry, stored_at + timedelta(seconds=_RETRY_SECONDS))
            return series, expiry
        except FileNotFoundError:
            return None
        except (OSError, KeyError, TypeError, ValueError) as exc:
            logger.warning("黄金缓存不可用 %s/%s: %s", key, kind, exc)
            return None

    def _read_attempt(self, key: SourceKey, now: datetime) -> tuple[DataCollectionRecord, datetime] | None:
        try:
            payload = json.loads(self._snapshot_path(key, "last_attempt").read_text(encoding="utf-8"))
            attempted = datetime.fromisoformat(payload["attempted_at"])
            record = DataCollectionRecord.model_validate(payload["record"])
            if (attempted.utcoffset() is None or not 0 <= (now - attempted).total_seconds() < _RETRY_SECONDS
                    or not record_consistent(record) or record.result_state != "failed"
                    or record.collected_at is not None):
                return None
            return record.model_copy(update={"cache_status": "cache"}), attempted
        except FileNotFoundError:
            return None
        except (OSError, KeyError, TypeError, ValueError) as exc:
            logger.warning("黄金失败尝试缓存不可用 %s: %s", key, exc)
            return None

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_cache(self, key: SourceKey, series: GoldSeries, now: datetime, kind: str = "latest",
                     *, expires_at: datetime | None = None) -> None:
        path = self._snapshot_path(key, kind)
        prior = None
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            prior = GoldSeries.model_validate(existing["series"])
        except FileNotFoundError:
            logger.debug("黄金快照尚不存在 %s/%s", key, kind)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            logger.debug("忽略不可读取的旧黄金快照 %s: %s", key, exc)
        if prior and prior.key == key and prior.unit == series.unit and prior.fetched_at > series.fetched_at:
            raise ValueError("已有较新的采集快照，拒绝迟到写入")
        expiry = expires_at or self.cache_expiry(key, now)
        if series.collection_record and series.collection_record.result_state == "partial":
            expiry = min(expiry, now + timedelta(seconds=_RETRY_SECONDS))
        self._atomic_json(path, {
            "expires_at": expiry.isoformat(), "stored_at": now.isoformat(),
            "series": series.model_dump(mode="json"),
        })

    def _write_attempt(self, key: SourceKey, record: DataCollectionRecord, now: datetime) -> None:
        stored_at = self._stored_at(key)
        if stored_at is not None and stored_at > now:
            logger.warning("黄金已有较新的成功记录，跳过迟到失败记录 %s", key)
            return
        path = self._snapshot_path(key, "last_attempt")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            previous = datetime.fromisoformat(payload["attempted_at"])
            if previous.utcoffset() is not None and previous > now:
                logger.warning("黄金已有较新的失败记录，跳过迟到失败记录 %s", key)
                return
        except (OSError, KeyError, TypeError, ValueError) as exc:
            logger.debug("黄金此前失败记录不可用 %s: %s", key, exc)
        self._atomic_json(path, {
            "attempted_at": now.isoformat(), "record": record.model_dump(mode="json"),
        })

    def _stored_at(self, key: SourceKey) -> datetime | None:
        try:
            payload = json.loads(self._snapshot_path(key, "latest").read_text(encoding="utf-8"))
            stamp = datetime.fromisoformat(payload.get("stored_at") or payload["series"]["fetched_at"])
            return stamp if stamp.utcoffset() is not None else None
        except (OSError, KeyError, TypeError, ValueError) as exc:
            logger.debug("黄金最近保存时间不可用 %s: %s", key, exc)
            return None

    def _reference(self, key: SourceKey, now: datetime, record: DataCollectionRecord,
                   latest: tuple[GoldSeries, datetime] | None,
                   complete: tuple[GoldSeries, datetime] | None) -> GoldSeries:
        snapshot = latest or complete
        if snapshot is None:
            result = empty_series(key, now, record.error_message or "本次未取得有效数据")
        else:
            result = snapshot[0].model_copy(deep=True)
            result.status = "stale"
            result.error = record.error_message or "本次刷新失败"
            result.notes.append("本次刷新失败，保留原观察日期和采集时间，仅作历史参考")
        result.collection_record = record
        return result

    def _request(self, key: SourceKey, now: datetime, deadline: float) -> CollectionResult:
        from gold.adapter import GoldSourceAdapter

        remaining = deadline - time.monotonic()
        if remaining <= 0 or not _REQUEST_SLOTS.acquire(timeout=max(0, remaining)):
            raise TimeoutError("等待黄金共享请求槽位时已到总体截止")
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("黄金采集总体预算已截止，未启动此来源")
            adapter = GoldSourceAdapter(self.request_executor, self.directory / "history",
                                        self.rules.historical_cache_seconds)
            return adapter.fetch_result("GOLD", key=key, now=now,
                                        timeout=min(self.rules.source_timeout_seconds, 30, remaining))
        finally:
            _REQUEST_SLOTS.release()

    @staticmethod
    def _validate_result(result: CollectionResult, key: SourceKey) -> tuple[GoldSeries | None, DataCollectionRecord]:
        if (not isinstance(result, CollectionResult) or result.record is None
                or not record_consistent(result.record) or result.record.error_kind == "contract_error"):
            raise TypeError("黄金来源返回的采集契约无效")
        record = result.record
        if not result.rows:
            if record.result_state != "failed" or record.collected_at is not None:
                raise TypeError("黄金失败诊断不符合契约")
            return None, record
        if (len(result.rows) != 1 or not isinstance(result.rows[0], GoldSeries)
                or record.result_state not in ("complete", "partial")):
            raise TypeError("黄金数据和诊断状态矛盾")
        value = result.rows[0]
        if (value.key != key or record.source != value.source or record.collected_at != value.fetched_at
                or (record.result_state == "complete" and (record.error_kind or record.error_message))):
            raise TypeError("黄金数据和来源诊断矛盾")
        return value, record

    def _collect_without_cache(self, key: SourceKey, now: datetime, deadline: float) -> GoldSeries:
        from gold.policy import classify_gold

        try:
            value, record = self._validate_result(self._request(key, now, deadline), key)
            if value is None:
                return self._reference(key, now, record, None, None)
            state = classify_gold(value, None, now, self.rules)
            if state not in ("complete", "partial"):
                raise ValueError("黄金数据未通过有效性校验")
            value.collection_record = make_record(value.source, state, diagnostic=record,
                                                  collected_at=value.fetched_at, cache_status="live")
            value.notes.append("缓存不可用，本次数据仍可使用")
            return value
        except (TimeoutError, TypeError, ValueError) as exc:
            logger.warning("黄金来源 %s 无缓存采集失败: %s", key, type(exc).__name__)
            kind = "contract_error" if isinstance(exc, TypeError) else "timeout" if isinstance(exc, TimeoutError) else "invalid_data"
            record = make_record(SOURCE_INFO[key][1], "failed", error_kind=kind, error_message="黄金来源采集失败")
            return self._reference(key, now, record, None, None)

    def _collect_source(self, key: SourceKey, now: datetime, deadline: float, refresh: bool) -> GoldSeries:
        from gold.policy import classify_gold

        requested_at = now
        started = deadline - min(self.rules.total_timeout_seconds, 60)

        def clock_now() -> datetime:
            return requested_at + timedelta(seconds=max(0, time.monotonic() - started))

        path = self._cache_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            remaining = max(0, deadline - time.monotonic())
            with FileLock(str(path) + ".lock", timeout=remaining):
                now = clock_now()
                latest = self._read_cache(key, now)
                complete = self._read_cache(key, now, "last_complete")
                complete_from_latest = False
                if complete is None and latest:
                    latest_at_fetch = classify_gold(latest[0], None, latest[0].fetched_at, self.rules)
                    if latest_at_fetch == "complete":
                        preserved = latest[0].model_copy(deep=True)
                        preserved.collection_record = make_record(preserved.source, "complete", collected_at=preserved.fetched_at)
                        complete = (preserved, latest[1])
                        complete_from_latest = True
                if not refresh:
                    attempt = self._read_attempt(key, now)
                    if attempt is not None:
                        failed, attempted = attempt
                        stored_at = self._stored_at(key) if latest else None
                        if stored_at is None or stored_at < attempted:
                            return self._reference(key, now, failed, latest, complete)
                    if latest and latest[1] > now:
                        value = latest[0].model_copy(deep=True)
                        assert value.collection_record is not None
                        value.collection_record = value.collection_record.model_copy(update={"cache_status": "cache"})
                        return value
                try:
                    value, record = self._validate_result(self._request(key, now, deadline), key)
                    finished = clock_now()
                    if value is None:
                        if record.error_kind != "contract_error":
                            try:
                                self._write_attempt(key, record, finished)
                            except (OSError, ValueError, TypeError) as exc:
                                logger.warning("黄金失败状态保存失败 %s: %s", key, exc)
                        return self._reference(key, now, record, latest, complete)
                    previous = complete[0] if complete else None
                    state = classify_gold(value, previous, finished, self.rules)
                    if state not in ("complete", "partial"):
                        raise ValueError("黄金数据未通过有效性校验")
                    if latest and latest[0].as_of and value.as_of and value.as_of < latest[0].as_of:
                        raise ValueError("来源观察日期早于已保存的最新日期")
                    value.collection_record = make_record(value.source, state, diagnostic=record,
                                                          collected_at=value.fetched_at, cache_status="live")
                    calendar_info = self.calendars[key](now) if key in self.calendars else source_calendar(key, now)
                    if calendar_info is not None and calendar_info.evidence:
                        value.metadata["cache_calendar_evidence"] = calendar_info.evidence
                        value.metadata["cache_calendar_timezone"] = str(calendar_info.next_update.tzinfo) if calendar_info.next_update else None
                    try:
                        if state == "partial" and complete_from_latest and complete is not None:
                            self._write_cache(key, complete[0], complete[0].fetched_at, "last_complete", expires_at=complete[1])
                        self._write_cache(key, value, finished)
                        if state == "complete":
                            self._write_cache(key, value, finished, "last_complete")
                        self._snapshot_path(key, "last_attempt").unlink(missing_ok=True)
                    except (OSError, ValueError, TypeError) as exc:
                        logger.warning("黄金缓存保存失败 %s: %s", key, exc)
                        value.notes.append("缓存保存失败，本次数据仍可使用")
                    return value
                except (TimeoutError, TypeError, ValueError) as exc:
                    logger.warning("黄金来源 %s 采集失败: %s", key, exc)
                    kind = "contract_error" if isinstance(exc, TypeError) else "invalid_data" if isinstance(exc, ValueError) else "timeout"
                    record = make_record(SOURCE_INFO[key][1], "failed", error_kind=kind, error_message=f"黄金来源采集失败（{type(exc).__name__}）")
                    if kind != "contract_error":
                        try:
                            self._write_attempt(key, record, clock_now())
                        except (OSError, ValueError, TypeError) as cache_exc:
                            logger.warning("黄金失败状态保存失败 %s: %s", key, cache_exc)
                    return self._reference(key, now, record, latest, complete)
        except Timeout as exc:
            logger.warning("等待黄金来源缓存锁超时 %s: %s", key, type(exc).__name__)
            record = make_record(SOURCE_INFO[key][1], "failed", error_kind="timeout", error_message="等待黄金数据时超时")
            finished = clock_now()
            return self._reference(key, finished, record, self._read_cache(key, finished), self._read_cache(key, finished, "last_complete"))
        except OSError as exc:
            logger.warning("黄金来源缓存锁不可用 %s: %s", key, type(exc).__name__)
            return self._collect_without_cache(key, clock_now(), deadline)

    def collect(self, *, now: datetime | None = None, refresh: bool = False) -> GoldCollection:
        now = now or local_now()
        if now.utcoffset() is None:
            raise ValueError("黄金采集时间必须含时区")
        deadline = time.monotonic() + min(self.rules.total_timeout_seconds, 60)
        result = GoldCollection(generated_at=now)
        pending = []
        for key in self.sources:
            if key == "global_reserves":
                series = empty_series(key, now, "IMF 全球数量序列尚未通过接入验收；不使用美元估值替代数量")
                series.collection_record = make_record(series.source, "failed", error_kind="unavailable", error_message=series.error or "不可用")
                result.series[key] = series
            else:
                pending.append(key)
        with ThreadPoolExecutor(max_workers=self.rules.max_workers, thread_name_prefix="gold-source") as pool:
            running: dict[Future[GoldSeries], SourceKey] = {}
            while pending or running:
                while pending and len(running) < self.rules.max_workers and time.monotonic() < deadline:
                    key = pending.pop(0)
                    running[pool.submit(self._collect_source, key, now, deadline, refresh)] = key
                if not running:
                    break
                completed, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in completed:
                    key = running.pop(future)
                    try:
                        result.series[key] = future.result()
                    except Exception as exc:  # noqa: BLE001 — 单来源隔离，不阻断整份黄金报告
                        logger.warning("黄金来源 %s 未能完成: %s", key, type(exc).__name__)
                        record = make_record(SOURCE_INFO[key][1], "failed", error_kind="unexpected", error_message="黄金来源未能完成")
                        result.series[key] = self._reference(
                            key, now, record,
                            self._read_cache(key, now), self._read_cache(key, now, "last_complete"),
                        )
            for key in pending:
                record = make_record(SOURCE_INFO[key][1], "failed", error_kind="timeout", error_message="黄金采集总体预算已截止")
                result.series[key] = self._reference(key, now, record, self._read_cache(key, now), self._read_cache(key, now, "last_complete"))
        if any(series.status != "ok" or (series.collection_record and series.collection_record.result_state != "complete") for series in result.series.values()):
            result.notes.append("部分来源缺失、数据不完整或更新失败；旧值保留原观察和采集日期")
        result.generated_at = now + timedelta(seconds=max(0, time.monotonic() - (deadline - min(self.rules.total_timeout_seconds, 60))))
        return result

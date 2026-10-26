"""财务、行情、A股新闻的当前事实与历史参考隔离；不管理其他数据路径。"""
import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Literal

from core.circuit_breaker import CircuitBreaker
from core.registry import Registry
from data.cache import CacheEntry, CacheManager
from data.hk_news import HK_TZ
from data.schemas import (
    DataCollectionRecord,
    DataReference,
    FinancialData,
    NewsData,
    PriceData,
    RawSentimentData,
)
from data.validation import financial_has_facts
from utils.config import Config

logger = logging.getLogger(__name__)
Kind = Literal["financial", "price", "news"]
State = Literal["complete", "degraded", "not_applicable", "unknown"]
RETRY_TTL = 1800
REFERENCE_AGES = {"financial": 30 * 86400, "price": 7 * 86400, "news": 86400}
TTL_KEYS = {"financial": "quarterly", "price": "daily", "news": "news"}


@dataclass
class Snapshot:
    rows: list[Any]
    record: DataCollectionRecord
    entry: CacheEntry


@dataclass
class CollectionOutcome:
    rows: list[Any] | None
    record: DataCollectionRecord
    reference: DataReference | None = None


def supports(market: str, kind: str) -> bool:
    return market in ("a-shares", "hk") and (kind in ("financial", "price") or (kind == "news" and market == "a-shares"))


class ResilientCollector:
    def __init__(self, cache: CacheManager, registry: Registry, breaker: CircuitBreaker, config: Config):
        self.cache, self.registry, self.breaker, self.config = cache, registry, breaker, config
        self._lock = Lock()
        self._request_locks: dict[tuple[str, Kind], Lock] = {}

    @staticmethod
    def _valid(kind: Kind, symbol: str, rows: list[Any]) -> bool:
        if not rows or any(row.symbol != symbol for row in rows):
            return False
        today = datetime.now(HK_TZ).date()
        if kind == "financial":
            return (financial_has_facts(rows, symbol)
                    and len({row.fiscal_quarter for row in rows}) == len(rows)
                    and all(row.fiscal_quarter <= today for row in rows))
        if kind == "news":
            return len(rows) == 1 and rows[0].date <= today
        return (len({row.trade_date for row in rows}) == len(rows) and all(
            row.trade_date <= today
            and all(math.isfinite(value) and value >= 0 for value in
                    (row.open, row.high, row.low, row.close, row.volume))
            and min(row.open, row.close, row.low) > 0
            and row.high >= max(row.open, row.close, row.low)
            and row.low <= min(row.open, row.close, row.high) for row in rows))

    @staticmethod
    def _price_requirements(previous: Snapshot | None, market: str) -> dict[str, Any]:
        if previous is None or not previous.rows:
            return {}
        today = datetime.now(HK_TZ).date()
        start = today - timedelta(days=365 if market == "hk" else 250)
        periods = [row.trade_date for row in previous.rows if start <= row.trade_date <= today]
        if not periods:
            return {}
        return {"minimum_end_date": max(periods), "minimum_points": max(60, len(periods))}

    def _state(self, kind: Kind, symbol: str, rows: list[Any], record: DataCollectionRecord,
               previous: Snapshot | None, market: str) -> State:
        if not self._valid(kind, symbol, rows):
            return "degraded"
        if record.cache_state == "not_applicable":
            return "not_applicable"
        if kind == "news":
            return "degraded" if record.error_kind or record.error_message else "complete"
        if kind == "price":
            requirements = self._price_requirements(previous, market)
            if len(rows) < requirements.get("minimum_points", 60):
                return "degraded"
            if requirements.get("minimum_end_date") and max(row.trade_date for row in rows) < requirements["minimum_end_date"]:
                return "degraded"
            return "complete"
        latest = max(rows, key=lambda row: row.fiscal_quarter)
        if latest.financial_selection_status == "complete":
            return "complete"
        if latest.financial_selection_status == "partial":
            return "degraded"
        from data.financial_selection import FinancialPackage

        if market == "hk":
            from data.hk_financial_policy import package_complete
        else:
            from data.a_financial_policy import package_complete
        return "complete" if package_complete(FinancialPackage("缓存", rows, {})) else "degraded"

    def _read(self, kind: Kind, symbol: str, key: str, market: str) -> Snapshot | None:
        entry = self.cache.get_entry(kind, symbol, key)
        if entry is None:
            return None
        try:
            data = json.loads(entry.value)
            if not isinstance(data, list):
                return None
            model = {"financial": FinancialData, "price": PriceData, "news": NewsData}[kind]
            rows = [model.model_validate(item) for item in data]
            if kind == "news" and rows and data[0].get("_raw_sentiment") is not None:
                rows[0]._raw_sentiment = RawSentimentData.model_validate(data[0]["_raw_sentiment"])
            record = (DataCollectionRecord.model_validate_json(entry.metadata)
                      if entry.metadata else DataCollectionRecord())
            if record.cache_state == "unknown":
                record = record.model_copy(update={"cache_state": self._state(kind, symbol, rows, record, None, market)})
            if rows and not self._valid(kind, symbol, rows):
                return None
            return Snapshot(rows, record, entry)
        except (ValueError, TypeError, AttributeError, KeyError):
            logger.debug("可靠性缓存格式无效 %s/%s/%s", symbol, kind, key)
            return None

    @staticmethod
    def _fresh(snapshot: Snapshot) -> bool:
        age = time.time() - snapshot.entry.created_at
        ttl = snapshot.entry.ttl_seconds
        if snapshot.record.cache_state == "degraded":
            ttl = RETRY_TTL if ttl < 0 else min(ttl, RETRY_TTL)
        return age >= 0 and (ttl < 0 or age <= ttl)

    def _reference(self, kind: Kind, previous: Snapshot | None, reason: str) -> DataReference | None:
        if previous is None or not previous.rows:
            return None
        stamp = previous.record.collected_at
        if stamp is None or stamp.utcoffset() is None:
            return None
        age = (datetime.now().astimezone() - stamp).total_seconds()
        if not 0 <= age <= REFERENCE_AGES[kind]:
            return None
        ref = DataReference(data_type=kind, record=previous.record.model_copy(deep=True), reason=reason)
        if kind == "financial":
            ref.financial_data = [row.model_copy(deep=True) for row in previous.rows]
        elif kind == "price":
            ref.price_data = [row.model_copy(deep=True) for row in previous.rows]
        else:
            news = previous.rows[0].model_copy(deep=True)
            raw = news._raw_sentiment
            if raw is None:
                return None  # 只有标题的旧缓存没有可核对的消息日期，不能冒充近30天内容。
            end = datetime.now(HK_TZ).date()
            raw.items = [item for item in raw.items if end-timedelta(days=29) <= item.publish_date <= end]
            news.headlines = [item.title for item in raw.items]
            ref.news_data = news
        return ref

    def _store(self, kind: Kind, symbol: str, rows: list[Any], record: DataCollectionRecord, market: str):
        try:
            with self._lock:
                if not rows and record.cache_state == "degraded":
                    # 失败只更新重试状态，不能抹掉已经观察到的较新部分事实。
                    ttl = self.config.get(f"data.cache_ttl.{TTL_KEYS[kind]}", 86400)
                    ttl = RETRY_TTL if ttl < 0 else min(ttl, RETRY_TTL)
                    self.cache.put(kind, symbol, "last_attempt", "[]", ttl_seconds=ttl,
                                   metadata=record.model_dump_json())
                    return
                previous = self._read(kind, symbol, "latest", market)
                backup = self._read(kind, symbol, "last_complete", market)
                if (previous and previous.record.cache_state == "complete"
                        and record.cache_state != "complete" and self._can_promote(previous, backup)):
                    self.cache.copy_entry(kind, symbol, "latest", "last_complete")
                data = []
                for row in rows:
                    item = row.model_dump(mode="json")
                    if kind == "news" and row._raw_sentiment is not None:
                        item["_raw_sentiment"] = row._raw_sentiment.model_dump(mode="json")
                    data.append(item)
                ttl = self.config.get(f"data.cache_ttl.{TTL_KEYS[kind]}", 86400)
                if record.cache_state == "degraded":
                    ttl = RETRY_TTL if ttl < 0 else min(ttl, RETRY_TTL)
                self.cache.put(kind, symbol, "latest", json.dumps(data, ensure_ascii=False),
                               ttl_seconds=ttl, metadata=record.model_dump_json())
                self.cache.invalidate(kind, symbol, "last_attempt")
                if record.cache_state == "complete":
                    self.cache.copy_entry(kind, symbol, "latest", "last_complete")
                self.cache.cleanup_old_entries(kind, symbol, "latest", preserve_date_keys=("last_complete",))
        except Exception as error:  # noqa: BLE001 — 缓存隔离不影响本次数据披露
            logger.warning("可靠性缓存写入失败 %s/%s: %s", symbol, kind, error)

    @staticmethod
    def _can_promote(candidate: Snapshot, backup: Snapshot | None) -> bool:
        stamp = candidate.record.collected_at
        if not candidate.record.source or stamp is None or stamp.utcoffset() is None:
            return False
        if stamp > datetime.now(HK_TZ):
            return False
        previous_stamp = backup.record.collected_at if backup else None
        return previous_stamp is None or previous_stamp.utcoffset() is None or stamp >= previous_stamp

    def collect(self, symbol: str, market: str, kind: Kind, refresh: bool) -> CollectionOutcome:
        # 仅串行化同一标的同类采集，避免迟到响应覆盖先完成的新事实；不同标的不阻塞。
        with self._lock:
            request_lock = self._request_locks.setdefault((symbol, kind), Lock())
        with request_lock:
            return self._collect(symbol, market, kind, refresh)

    def _collect(self, symbol: str, market: str, kind: Kind, refresh: bool) -> CollectionOutcome:
        latest = self._read(kind, symbol, "latest", market)
        complete = self._read(kind, symbol, "last_complete", market)
        attempt = self._read(kind, symbol, "last_attempt", market)
        if complete is None and latest and latest.record.cache_state == "complete":
            complete = latest
        if attempt and not refresh and self._fresh(attempt):
            record = attempt.record.model_copy(update={"cache_status": "cache"})
            reference = self._reference(kind, latest, "本次刷新失败；以下仅为历史参考，不计分")
            return CollectionOutcome(None, record, reference or self._reference(kind, complete, "本次刷新失败；以下仅为历史参考，不计分"))
        if latest and attempt is None and not refresh and self._fresh(latest):
            record = latest.record.model_copy(update={"cache_status": "cache"})
            if latest.rows:
                self.breaker.record_success(symbol, kind)
            reference = (self._reference(kind, complete, "本次数据不完整；以下仅为历史参考，不计分")
                         if record.cache_state == "degraded" else None)
            return CollectionOutcome(latest.rows or None, record, reference)

        failure = DataCollectionRecord(cache_status="live", cache_state="degraded",
                                       error_kind="empty", error_message="本次未取得有效数据")
        if refresh or not self.breaker.is_open(symbol, kind):
            for source in self.registry.get_data_sources(market, kind):
                for attempt in range(2):
                    try:
                        kwargs: dict[str, Any] = {"data_type": kind, "market": market}
                        if kind == "price":
                            requirements = [self._price_requirements(item, market) for item in (complete, latest)]
                            requirements = [item for item in requirements if item]
                            if requirements:
                                kwargs.update(minimum_end_date=max(item["minimum_end_date"] for item in requirements),
                                              minimum_points=max(item["minimum_points"] for item in requirements))
                        rows = source.fetch(symbol, **kwargs)
                        reader = getattr(source, "get_collection_error", None)
                        diagnostic = reader() if callable(reader) else None
                        if not rows and isinstance(diagnostic, DataCollectionRecord) and diagnostic.cache_state == "not_applicable":
                            record = diagnostic.model_copy(update={"cache_status": "live",
                                "source": diagnostic.source or source.__class__.__name__, "collected_at": datetime.now().astimezone()})
                            self._store(kind, symbol, [], record, market)
                            self.breaker.record_success(symbol, kind)
                            return CollectionOutcome(None, record)
                        if isinstance(diagnostic, DataCollectionRecord):
                            failure = diagnostic.model_copy(update={"cache_status": "live", "cache_state": "degraded"})
                        valid = self._valid(kind, symbol, rows)
                        if valid and latest and latest.rows:
                            if kind == "financial" and max(row.fiscal_quarter for row in rows) < max(row.fiscal_quarter for row in latest.rows):
                                valid = False
                            if kind == "price" and max(row.trade_date for row in rows) < max(row.trade_date for row in latest.rows):
                                valid = False
                        if valid:
                            record = DataCollectionRecord(source=getattr(rows[0], "_data_source", "") or source.__class__.__name__,
                                collected_at=datetime.now().astimezone(), cache_status="live")
                            if isinstance(diagnostic, DataCollectionRecord):
                                record = record.model_copy(update={"error_kind": diagnostic.error_kind,
                                    "error_message": diagnostic.error_message, "cache_state": diagnostic.cache_state})
                            state = self._state(kind, symbol, rows, record, complete, market)
                            record.cache_state = state
                            if state == "degraded" and not record.error_message:
                                record.error_kind, record.error_message = "incomplete", "字段或历史覆盖不足，暂按降级结果等待后续请求重试"
                            self._store(kind, symbol, rows, record, market)
                            self.breaker.record_success(symbol, kind)
                            reference = (self._reference(kind, complete, "本次数据不完整；以下仅为历史参考，不计分")
                                         if state == "degraded" else None)
                            return CollectionOutcome(rows, record, reference)
                        failure = failure.model_copy(update={"error_kind": failure.error_kind or "invalid_data"})
                    except Exception as error:  # noqa: BLE001 — 单个来源失败仍尝试已有备用
                        logger.warning("可靠性采集失败 %s/%s/%s: %s", symbol, kind, source.__class__.__name__, type(error).__name__)
                        timeout = "Timeout" in type(error).__name__
                        failure = DataCollectionRecord(source=source.__class__.__name__, cache_status="live", cache_state="degraded",
                            error_kind="timeout" if timeout else "fetch_failed", error_message="数据请求超时" if timeout else "数据请求失败")
                    if attempt == 0:
                        time.sleep(1)
            self.breaker.record_failure(symbol, kind)
        else:
            failure.error_kind, failure.error_message = "circuit_open", "数据源暂时熔断，等待后续请求重试"
        self._store(kind, symbol, [], failure, market)
        reference = self._reference(kind, latest, "本次刷新失败；以下仅为历史参考，不计分")
        return CollectionOutcome(None, failure, reference or self._reference(kind, complete, "本次刷新失败；以下仅为历史参考，不计分"))

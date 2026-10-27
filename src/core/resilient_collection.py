"""财务、行情、A股新闻的当前事实与历史参考隔离；不管理其他数据路径。"""
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any, Literal

from core.circuit_breaker import CircuitBreaker
from core.collection_contract import MIGRATED_CAPABILITIES
from core.registry import Registry
from data.base import CollectionResult, DataSource
from data.cache import CacheEntry, CacheManager
from data.collection_policies import POLICIES, CollectionPolicy, policy_for
from data.hk_news import HK_TZ
from data.schemas import (
    DataCollectionRecord,
    DataReference,
    FinancialData,
    NewsData,
    PriceData,
    RawSentimentData,
    ResultState,
)
from utils.config import Config

logger = logging.getLogger(__name__)
Kind = Literal["financial", "price", "news"]
State = Literal["complete", "degraded", "not_applicable", "unknown"]
RETRY_TTL = 1800
CACHE_STATE: dict[ResultState, State] = {
    "complete": "complete",
    "partial": "degraded",
    "empty": "complete",
    "not_applicable": "not_applicable",
    "failed": "degraded",
    "unknown": "unknown",
}


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
    return (market, kind) in MIGRATED_CAPABILITIES


class CollectionContractError(ValueError):
    """已迁移能力的策略或统一入口缺失。"""


class ResilientCollector:
    def __init__(self, cache: CacheManager, registry: Registry, breaker: CircuitBreaker, config: Config):
        self.cache, self.registry, self.breaker, self.config = cache, registry, breaker, config
        self._lock = Lock()
        self._request_locks: dict[tuple[str, Kind], Lock] = {}
        self._validate_contract()

    @staticmethod
    def _has_explicit_fetch_result(source: DataSource) -> bool:
        return type(source).fetch_result is not DataSource.fetch_result

    def _validate_contract(self) -> None:
        if set(POLICIES) != set(MIGRATED_CAPABILITIES):
            raise CollectionContractError("五组能力的策略注册不完整")
        for market, kind in MIGRATED_CAPABILITIES:
            policy = policy_for(market, kind)
            if policy is None:
                raise CollectionContractError(f"缺少采集策略：{market}/{kind}")
            sources = self.registry.get_data_sources(market, kind)
            if not isinstance(sources, list):
                continue
            for source in sources:
                if not self._has_explicit_fetch_result(source):
                    raise CollectionContractError(
                        f"数据源 {source.__class__.__name__} 未实现统一取数入口 fetch_result"
                    )

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
            record = (
                DataCollectionRecord.model_validate_json(entry.metadata)
                if entry.metadata else DataCollectionRecord()
            )
            if record.result_state != "unknown" and not self._record_consistent(record):
                logger.error("缓存采集记录状态矛盾 %s/%s/%s", symbol, kind, key)
                return None
            policy = policy_for(market, kind)
            if policy is None:
                return None
            now = datetime.now(HK_TZ)
            if key == "last_attempt" and record.result_state == "unknown":
                record = record.model_copy(
                    update={"result_state": "failed", "cache_state": "degraded"}
                )
            if rows:
                if not policy.validate(symbol, rows, now):
                    return None
                if record.result_state in ("unknown", "failed"):
                    previous = (
                        self._read(kind, symbol, "last_complete", market)
                        if key == "latest" else None
                    )
                    state = policy.classify(
                        symbol, rows, record,
                        previous.rows if previous and previous.rows else None, now,
                    )
                    record = record.model_copy(
                        update={"result_state": state, "cache_state": CACHE_STATE[state]}
                    )
            elif record.result_state == "unknown" and record.cache_state == "not_applicable":
                record = record.model_copy(update={"result_state": "not_applicable"})
            elif record.result_state not in ("not_applicable", "failed"):
                return None
            return Snapshot(rows, record, entry)
        except (ValueError, TypeError, AttributeError, KeyError):
            logger.debug("可靠性缓存格式无效 %s/%s/%s", symbol, kind, key)
            return None

    @staticmethod
    def _fresh(snapshot: Snapshot) -> bool:
        age = time.time() - snapshot.entry.created_at
        ttl = snapshot.entry.ttl_seconds
        if snapshot.record.result_state in ("partial", "failed", "unknown"):
            ttl = RETRY_TTL if ttl < 0 else min(ttl, RETRY_TTL)
        return age >= 0 and (ttl < 0 or age <= ttl)

    def _reference(self, kind: Kind, market: str, previous: Snapshot | None,
                   reason: str) -> DataReference | None:
        if previous is None or not previous.rows:
            return None
        stamp = previous.record.collected_at
        if stamp is None or stamp.utcoffset() is None:
            return None
        policy = policy_for(market, kind)
        if policy is None:
            return None
        age = (datetime.now().astimezone() - stamp).total_seconds()
        if not 0 <= age <= policy.reference_age:
            return None
        rows = policy.reference_rows(previous.rows, datetime.now(HK_TZ))
        if rows is None:
            return None
        ref = DataReference(
            data_type=kind,
            record=previous.record.model_copy(deep=True),
            reason=reason,
        )
        if kind == "financial":
            ref.financial_data = [row.model_copy(deep=True) for row in rows]
        elif kind == "price":
            ref.price_data = [row.model_copy(deep=True) for row in rows]
        else:
            news = rows[0].model_copy(deep=True)
            if news._raw_sentiment is None:
                return None
            ref.news_data = news
        return ref

    def _normal_ttl(self, policy: CollectionPolicy) -> int:
        return int(self.config.get(f"data.cache_ttl.{policy.ttl_key}", 86400))

    def _wait_ttl(self, policy: CollectionPolicy) -> int:
        normal = self._normal_ttl(policy)
        return RETRY_TTL if normal < 0 else min(normal, RETRY_TTL)

    @staticmethod
    def _record_consistent(record: DataCollectionRecord) -> bool:
        return record.cache_state == CACHE_STATE.get(record.result_state)

    def _store(self, kind: Kind, symbol: str, rows: list[Any],
               record: DataCollectionRecord, market: str) -> None:
        try:
            with self._lock:
                policy = policy_for(market, kind)
                if (policy is None or record.result_state == "unknown"
                        or not self._record_consistent(record)):
                    logger.error("拒绝写入不一致的采集记录 %s/%s", symbol, kind)
                    return
                if record.error_kind == "contract_error":
                    logger.error("拒绝缓存契约错误 %s/%s", symbol, kind)
                    return
                if record.result_state == "failed":
                    self.cache.put(
                        kind, symbol, "last_attempt", "[]",
                        ttl_seconds=self._wait_ttl(policy),
                        metadata=record.model_dump_json(),
                    )
                    return
                previous = self._read(kind, symbol, "latest", market)
                backup = self._read(kind, symbol, "last_complete", market)
                if (previous and previous.record.result_state in ("complete", "empty")
                        and record.result_state not in ("complete", "empty")
                        and self._can_promote(previous, backup)):
                    self.cache.copy_entry(kind, symbol, "latest", "last_complete")
                data = []
                for row in rows:
                    item = row.model_dump(mode="json")
                    if kind == "news" and row._raw_sentiment is not None:
                        item["_raw_sentiment"] = row._raw_sentiment.model_dump(mode="json")
                    data.append(item)
                ttl = self._normal_ttl(policy)
                if record.result_state == "partial":
                    ttl = self._wait_ttl(policy)
                self.cache.put(
                    kind, symbol, "latest", json.dumps(data, ensure_ascii=False),
                    ttl_seconds=ttl, metadata=record.model_dump_json(),
                )
                self.cache.invalidate(kind, symbol, "last_attempt")
                if record.result_state in ("complete", "empty"):
                    self.cache.copy_entry(kind, symbol, "latest", "last_complete")
                self.cache.cleanup_old_entries(
                    kind, symbol, "latest", preserve_date_keys=("last_complete",)
                )
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
        policy = policy_for(market, kind)
        if policy is None:
            return CollectionOutcome(None, self._contract_failure("", "缺少采集策略"))
        latest = self._read(kind, symbol, "latest", market)
        complete = self._read(kind, symbol, "last_complete", market)
        attempt = self._read(kind, symbol, "last_attempt", market)
        if complete is None and latest and latest.record.result_state in ("complete", "empty"):
            complete = latest
        if attempt and not refresh and self._fresh(attempt):
            record = attempt.record.model_copy(update={"cache_status": "cache"})
            reference = self._reference(
                kind, market, latest, "本次刷新失败；以下仅为历史参考，不计分"
            )
            return CollectionOutcome(
                None, record,
                reference or self._reference(
                    kind, market, complete, "本次刷新失败；以下仅为历史参考，不计分"
                ),
            )
        if latest and attempt is None and not refresh and self._fresh(latest):
            record = latest.record.model_copy(update={"cache_status": "cache"})
            if latest.rows:
                self.breaker.record_success(symbol, kind)
            reference = (
                self._reference(
                    kind, market, complete, "本次数据不完整；以下仅为历史参考，不计分"
                )
                if record.result_state == "partial" else None
            )
            return CollectionOutcome(latest.rows or None, record, reference)

        failure = DataCollectionRecord(
            cache_status="live", cache_state="degraded", result_state="failed",
            error_kind="empty", error_message="本次未取得有效数据",
        )
        contract_error = False
        if refresh or not self.breaker.is_open(symbol, kind):
            sources = self.registry.get_data_sources(market, kind)
            for source in sources:
                if not self._has_explicit_fetch_result(source):
                    contract_error = True
                    failure = self._contract_failure(
                        source.__class__.__name__, "数据源未实现统一取数入口 fetch_result"
                    )
                    continue
                for attempt_index in range(policy.source_attempts):
                    try:
                        kwargs: dict[str, Any] = {"data_type": kind, "market": market}
                        if kind == "price":
                            kwargs.update(self._price_constraints(policy, complete, latest))
                        result = source.fetch_result(symbol, **kwargs)
                        if not isinstance(result, CollectionResult):
                            contract_error = True
                            failure = self._contract_failure(
                                source.__class__.__name__, "fetch_result 返回类型无效"
                            )
                            continue
                        rows = result.rows or []
                        diagnostic = result.record
                        state = policy.classify(
                            symbol, rows, diagnostic,
                            complete.rows if complete and complete.rows else None,
                            datetime.now(HK_TZ),
                        )
                        if state == "not_applicable":
                            record = self._live_record(
                                self._source_label(rows, source, diagnostic), state, diagnostic
                            )
                            self._store(kind, symbol, rows, record, market)
                            self.breaker.record_success(symbol, kind)
                            return CollectionOutcome(rows or None, record)
                        if state == "failed":
                            failure = self._failure_record(source, diagnostic)
                            continue
                        if self._is_late(kind, rows, latest):
                            failure = self._failure_record(
                                source, diagnostic, "invalid_data",
                                "返回数据早于已观察事实",
                            )
                            continue
                        record = self._live_record(
                            self._source_label(rows, source, diagnostic), state, diagnostic
                        )
                        if state == "partial" and not record.error_message:
                            record = record.model_copy(update={
                                "error_kind": "incomplete",
                                "error_message": "字段或历史覆盖不足，暂按降级结果等待后续请求重试",
                            })
                        self._store(kind, symbol, rows, record, market)
                        self.breaker.record_success(symbol, kind)
                        reference = (
                            self._reference(
                                kind, market, complete,
                                "本次数据不完整；以下仅为历史参考，不计分",
                            )
                            if state == "partial" else None
                        )
                        return CollectionOutcome(rows or None, record, reference)
                    except Exception as error:  # noqa: BLE001 — 单个来源失败仍尝试已有备用
                        logger.warning(
                            "可靠性采集失败 %s/%s/%s: %s", symbol, kind,
                            source.__class__.__name__, type(error).__name__,
                        )
                        timeout = "Timeout" in type(error).__name__
                        failure = DataCollectionRecord(
                            source=source.__class__.__name__, cache_status="live",
                            cache_state="degraded", result_state="failed",
                            error_kind="timeout" if timeout else "fetch_failed",
                            error_message="数据请求超时" if timeout else "数据请求失败",
                        )
                    if attempt_index == 0 and policy.source_attempts > 1:
                        time.sleep(1)
            if not contract_error:
                self.breaker.record_failure(symbol, kind)
        else:
            failure = failure.model_copy(update={
                "error_kind": "circuit_open",
                "error_message": "数据源暂时熔断，等待后续请求重试",
            })
        if not contract_error:
            self._store(kind, symbol, [], failure, market)
        reference = self._reference(
            kind, market, latest, "本次刷新失败；以下仅为历史参考，不计分"
        )
        return CollectionOutcome(
            None, failure,
            reference or self._reference(
                kind, market, complete, "本次刷新失败；以下仅为历史参考，不计分"
            ),
        )

    @staticmethod
    def _price_constraints(policy: CollectionPolicy, complete: Snapshot | None,
                           latest: Snapshot | None) -> dict[str, Any]:
        constraints = [
            policy.fetch_constraints(snapshot.rows, datetime.now(HK_TZ))
            for snapshot in (complete, latest) if snapshot and snapshot.rows
        ]
        if not constraints:
            return {}
        result: dict[str, Any] = {
            "minimum_points": max(int(item.get("minimum_points", 60)) for item in constraints)
        }
        ends = [item["minimum_end_date"] for item in constraints if item.get("minimum_end_date")]
        if ends:
            result["minimum_end_date"] = max(ends)
        return result

    @staticmethod
    def _is_late(kind: Kind, rows: list[Any], latest: Snapshot | None) -> bool:
        if latest is None or not latest.rows or not rows:
            return False
        if kind == "financial":
            return max(row.fiscal_quarter for row in rows) < max(
                row.fiscal_quarter for row in latest.rows
            )
        if kind == "price":
            return max(row.trade_date for row in rows) < max(
                row.trade_date for row in latest.rows
            )
        return False

    @staticmethod
    def _source_label(rows: list[Any], source: DataSource,
                      diagnostic: DataCollectionRecord | None) -> str:
        if rows:
            return getattr(rows[0], "_data_source", "") or (
                diagnostic.source if diagnostic and diagnostic.source
                else source.__class__.__name__
            )
        return diagnostic.source if diagnostic and diagnostic.source else source.__class__.__name__

    @staticmethod
    def _live_record(source: str, state: ResultState,
                     diagnostic: DataCollectionRecord | None) -> DataCollectionRecord:
        return DataCollectionRecord(
            source=source,
            collected_at=datetime.now().astimezone(),
            cache_status="live",
            error_kind=diagnostic.error_kind if diagnostic else "",
            error_message=diagnostic.error_message if diagnostic else "",
            cache_state=CACHE_STATE[state],
            result_state=state,
        )

    @staticmethod
    def _failure_record(source: DataSource, diagnostic: DataCollectionRecord | None,
                        error_kind: str = "empty",
                        error_message: str = "本次未取得有效数据") -> DataCollectionRecord:
        if diagnostic is not None and (diagnostic.error_kind or diagnostic.error_message):
            return diagnostic.model_copy(update={
                "source": diagnostic.source or source.__class__.__name__,
                "cache_status": "live",
                "cache_state": "degraded",
                "result_state": "failed",
                "collected_at": None,
            })
        return DataCollectionRecord(
            source=source.__class__.__name__, cache_status="live",
            cache_state="degraded", result_state="failed",
            error_kind=error_kind, error_message=error_message,
        )

    @staticmethod
    def _contract_failure(source: str, message: str) -> DataCollectionRecord:
        return DataCollectionRecord(
            source=source, cache_status="live", cache_state="degraded",
            result_state="failed", error_kind="contract_error", error_message=message,
        )

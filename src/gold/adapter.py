"""黄金专用来源的统一数据与诊断入口。"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from data.base import CollectionResult, DataSource
from data.collection_state import make_record
from gold.models import GoldSeries
from gold.sources import SOURCE_INFO
from radar.provider_request import ProviderRequestError

logger = logging.getLogger(__name__)


class GoldSourceAdapter(DataSource):
    """包装原有可终止来源调用，不扩大发送次数或超时预算。"""

    def __init__(self, request_executor: Callable[..., Any], history_dir: Path,
                 historical_cache_seconds: int) -> None:
        self.request_executor = request_executor
        self.history_dir = Path(history_dir)
        self.historical_cache_seconds = historical_cache_seconds

    def supports(self, market: str, data_type: str) -> bool:
        key = data_type.removeprefix("gold_")
        return market == "gold" and key in SOURCE_INFO

    @staticmethod
    def _failure(source: str, kind: str, message: str) -> CollectionResult:
        return CollectionResult(rows=[], record=make_record(
            source, "failed", cache_status="live", error_kind=kind, error_message=message,
        ))

    def _collect(self, symbol: str, **kwargs: Any) -> CollectionResult:
        key = kwargs.get("key")
        now = kwargs.get("now")
        timeout = kwargs.get("timeout")
        if (symbol != "GOLD" or not isinstance(key, str) or key not in SOURCE_INFO
                or not isinstance(now, datetime) or now.utcoffset() is None
                or isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            return self._failure("黄金来源", "contract_error", "黄金来源请求参数无效")
        source = SOURCE_INFO[key][1]
        if key == "global_reserves":
            return self._failure(source, "unavailable", "IMF 全球数量序列尚未通过接入验收")
        try:
            result = self.request_executor(
                "gold.sources", "fetch_source",
                {"key": key, "now_iso": now.isoformat(), "history_dir": str(self.history_dir),
                 "historical_cache_seconds": self.historical_cache_seconds}, timeout=timeout,
            )
        except TimeoutError:
            logger.warning("黄金来源 %s 请求超时", key)
            return self._failure(source, "timeout", "黄金来源请求超时")
        except ProviderRequestError as exc:
            if str(exc).startswith("数据源请求超过 "):
                logger.warning("黄金来源 %s 请求超时", key)
                return self._failure(source, "timeout", "黄金来源请求超时")
            logger.warning("黄金来源 %s 请求进程失败", key)
            return self._failure(source, "fetch_failed", "黄金来源请求进程失败")
        except Exception as exc:  # noqa: BLE001 — 第三方网络及隔离进程边界仅记录异常类型
            logger.warning("黄金来源 %s 请求失败：%s", key, type(exc).__name__)
            return self._failure(source, "fetch_failed", f"黄金来源请求失败（{type(exc).__name__}）")
        try:
            series = GoldSeries.model_validate(result)
        except (ValueError, TypeError) as exc:
            logger.warning("黄金来源 %s 返回无效序列：%s", key, type(exc).__name__)
            return self._failure(source, "invalid_data", "黄金来源数据无效或不完整")
        if series.key != key or series.unit != SOURCE_INFO[key][3]:
            return self._failure(source, "invalid_data", "黄金序列身份或单位无效")
        if (series.fetched_at > now
                or any(point.observation_date > now.date() for point in series.observations)
                or any(bar.trade_date > now.date() for bar in series.bars)):
            return self._failure(source, "invalid_data", "黄金序列包含未来日期")
        if series.status != "ok" or not (series.bars or series.observations):
            return self._failure(source, "invalid_data", "黄金来源未返回有效数据")
        return CollectionResult(rows=[series], record=make_record(
            source, "complete", collected_at=series.fetched_at, cache_status="live",
        ))

    def fetch_result(self, symbol: str, **kwargs: Any) -> CollectionResult:
        return self._collect(symbol, **kwargs)

    def fetch(self, symbol: str, **kwargs: Any) -> list[Any]:
        return self._collect(symbol, **kwargs).rows

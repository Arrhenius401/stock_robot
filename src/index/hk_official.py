"""恒生官方收盘历史及香港金管局市场背景解析。"""
import json
import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from data.cache import CacheManager
from data.collection_state import ValueResult, make_record
from index.strategy_data import finite_number

HISTORY_URL = "https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/v1/product-data/pub/indexes/metadata/v2"
HKMA_URL = "https://api.hkma.gov.hk/public/market-data-and-statistics/daily-monetary-statistics/daily-figures-interbank-liquidity"


def parse_history(payload: dict[str, Any], code: str) -> list[tuple[date, float]]:
    """仅返回与请求身份一致的有效收盘价，不构造高低价。"""
    if payload.get("code") != "0" or not isinstance(payload.get("data"), list):
        raise ValueError("恒生历史响应无效")
    items = payload["data"]
    if len(items) != 1 or items[0].get("indexCode") != code:
        raise ValueError("恒生历史指数身份不匹配")
    history = items[0].get("valueHistory")
    if not isinstance(history, list):
        raise TypeError("恒生历史缺少数据")
    rows: dict[date, float] = {}
    for item in history:
        try:
            day = date.fromisoformat(item["date"])
            close = finite_number(item["close"])
        except (ValueError, TypeError, KeyError):
            continue
        if close is None or close <= 0 or day > datetime.now(ZoneInfo("Asia/Hong_Kong")).date():
            continue
        if day in rows and rows[day] != close:
            raise ValueError("恒生历史存在重复日期冲突")
        rows[day] = close
    if not rows:
        raise ValueError("恒生历史无有效收盘价")
    return sorted(rows.items())


def parse_hkma(payload: dict[str, Any], as_of: date, *, max_age_days: int) -> dict[str, Any]:
    """提取最近有效金融背景；按调用方指定的自然日阈值判断时效。"""
    if payload.get("header", {}).get("success") is not True:
        raise ValueError("金管局响应失败")
    records = payload.get("result", {}).get("records")
    if not isinstance(records, list):
        raise TypeError("金管局记录无效")
    candidates = []
    for row in records:
        try:
            day = date.fromisoformat(row["end_of_date"])
        except (ValueError, TypeError, KeyError):
            continue
        if day <= as_of:
            candidates.append((day, row))
    if not candidates:
        raise ValueError("金管局数据缺少有效日期")
    day, row = max(candidates, key=lambda item: item[0])
    if (as_of - day).days > max_age_days:
        raise ValueError("金管局数据已过期")
    result = {"date": day, "hibor_1m": finite_number(row.get("hibor_fixing_1m")),
              "base_rate": finite_number(row.get("disc_win_base_rate")),
              "closing_balance_hkd_million": finite_number(row.get("closing_balance"))}
    if all(value is None for key, value in result.items() if key != "date"):
        raise ValueError("金管局数据缺少有效指标")
    return result




logger = logging.getLogger(__name__)


def _request(url: str, params: dict[str, str]) -> dict[str, Any]:
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("数据源响应不是对象")
    return payload


class HongKongOfficialProvider:
    """按身份缓存已验证的官方响应，刷新失败保留旧数据。"""

    def __init__(self, cache: CacheManager, request: Callable | None = None):
        self.cache = cache
        self.request = request or _request

    def _load(self, kind, key, url, params, parser, refresh):
        entry = self.cache.get_entry(kind, key, "v1")
        old = None
        if entry:
            try:
                old = parser(json.loads(entry.value))
            except (TypeError, ValueError, KeyError):
                logger.warning("缓存数据无效 %s/%s", kind, key)
        if old is not None and not refresh and entry is not None:
            import time
            if entry.ttl_seconds < 0 or time.time() - entry.created_at <= entry.ttl_seconds:
                return ValueResult(old, make_record(kind, "complete", cache_status="cache",
                    collected_at=datetime.fromtimestamp(entry.created_at).astimezone()))
        try:
            payload = self.request(url, params)
            value = parser(payload)
            self.cache.put(kind, key, "v1", json.dumps(payload, ensure_ascii=False), ttl_seconds=86400)
            return ValueResult(value, make_record(kind, "complete", collected_at=datetime.now().astimezone()))
        except Exception as exc:  # noqa: BLE001 — 外部服务和解析隔离，保留成功快照
            logger.warning("%s 采集失败：%s", kind, exc)
            if old is not None and entry is not None:
                return ValueResult(old, make_record(kind, "partial", cache_status="stale",
                    collected_at=datetime.fromtimestamp(entry.created_at).astimezone(),
                    error_kind="fetch_failed", error_message="刷新失败，使用原日期数据"), [str(exc)])
            return ValueResult(None, make_record(kind, "failed", error_kind="fetch_failed",
                error_message="采集失败或数据无效"), [str(exc)])

    def history(self, code: str, *, refresh: bool = False):
        return self._load("恒生官方历史", code, HISTORY_URL,
            {"data": "valueHistoryPub", "indexCodes": code, "language": "en-hk"},
            lambda payload: parse_history(payload, code), refresh)

    def macro(self, as_of: date, *, max_age_days: int, refresh: bool = False):
        return self._load("香港金管局", "market", HKMA_URL,
            {"pagesize": "10", "sortby": "end_of_date", "sortorder": "desc"},
            lambda payload: parse_hkma(payload, as_of, max_age_days=max_age_days), refresh)

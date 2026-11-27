"""ECB 日频参考汇率；保留实际日期，失败不猜测、不补造。"""
import json
import logging
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime
from functools import lru_cache
from itertools import pairwise
from typing import Any

import requests

from data.cache import CacheManager
from utils.paths import project_state_dir

logger = logging.getLogger(__name__)
SOURCE = "ECB reference rates via Frankfurter"
BASE_URL = "https://api.frankfurter.app"


@dataclass(frozen=True)
class FxRate:
    currency: str
    target: str
    rate: float | None = None
    dates: tuple[date, ...] = ()
    reason: str = ""
    source: str = SOURCE

    def payload(self) -> dict[str, Any]:
        return {**asdict(self), "dates": [day.isoformat() for day in self.dates]}

    def note(self) -> str:
        if self.rate is None:
            return f"汇率未核实，未统一 CNY；源币种 {self.currency or '未核实'}；{self.reason}"
        days = "、".join(day.isoformat() for day in self.dates) if len(self.dates) < 3 else (
            f"{self.dates[0]} 至 {self.dates[-1]}，{len(self.dates)} 个日频参考值的算术平均")
        return f"展示 {self.target}；源币种 {self.currency}；汇率 {self.rate:.8g}（{days}）；{self.source}"


@lru_cache(maxsize=1)
def _source_cache() -> CacheManager:
    return CacheManager(project_state_dir() / "fx-rates.db")


def _request(currency: str, target: str, window: str) -> Any:
    response = requests.get(f"{BASE_URL}/{window}", params={"from": currency, "to": target}, timeout=15)
    response.raise_for_status()
    return response.json()


def _load(currency: str, target: str, window: str) -> dict[date, float]:
    """缓存成功响应及其中的日频数据；请求别名保留源实际日期。"""
    cache = _source_cache()
    key = f"{currency}-{target}"
    saved = cache.get("fx_daily", key, window)
    if saved is not None:
        payload = json.loads(saved)
    else:
        payload = _request(currency, target, window)
    if not isinstance(payload, dict) or payload.get("base") != currency or payload.get("amount") != 1:
        raise ValueError("汇率源响应基准币种或金额异常")
    rates = payload.get("rates")
    if not isinstance(rates, dict):
        raise TypeError("汇率源缺少日频数据")
    entries = {payload["date"]: rates} if "date" in payload else rates
    result: dict[date, float] = {}
    for day, values in entries.items():
        value = values.get(target) if isinstance(values, dict) else None
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
            continue
        result[date.fromisoformat(day)] = float(value)
    if not result:
        raise ValueError("汇率源没有覆盖请求币种的数据")
    if saved is None:
        # 日期别名可能指向上一交易日，短缓存允许之后取得新公布值。
        cache.put("fx_daily", key, window, json.dumps(payload), ttl_seconds=3600)
    return result


def rate_on(currency: str, target: str = "CNY", *, day: date) -> FxRate:
    """请求日或之前最近可得值；返回源日期，绝不将旧值标为请求日。"""
    if currency == target and currency:
        return FxRate(currency, target, 1.0, (day,), reason="同币种直通，无需换汇")
    if currency not in {"HKD", "USD", "CNY"} or target != "CNY":
        return FxRate(currency, target, reason="源币种未核实或不支持该币对")
    try:
        rows = _load(currency, target, day.isoformat())
        candidates = [stamp for stamp in rows if stamp <= day]
        if not candidates:
            return FxRate(currency, target, reason="请求日及之前无覆盖")
        actual = max(candidates)
        return FxRate(currency, target, rows[actual], (actual,))
    except Exception as error:  # noqa: BLE001 — 网络/缓存边界，返回明确失败原因
        logger.warning("汇率获取失败 %s/%s %s: %s", currency, target, day, error)
        return FxRate(currency, target, reason=f"{type(error).__name__}: {error}")


def average_rate(currency: str, start: date, end: date, target: str = "CNY") -> FxRate:
    """期间内已公布日频参考汇率的算术平均，保留所有参与日期。"""
    if start > end or end > datetime.now().astimezone().date():
        return FxRate(currency, target, reason="汇率期间无效或尚未结束")
    if currency == target and currency:
        return FxRate(currency, target, 1.0, (start, end), reason="同币种直通，无需换汇")
    if currency not in {"HKD", "USD", "CNY"} or target != "CNY":
        return FxRate(currency, target, reason="源币种未核实或不支持该币对")
    try:
        rows = _load(currency, target, f"{start}..{end}")
        days = tuple(sorted(day for day in rows if start <= day <= end))
        if (not days or (days[0] - start).days > 7 or (end - days[-1]).days > 7
                or any((right - left).days > 7 for left, right in pairwise(days))):
            return FxRate(currency, target, reason="汇率源没有完整期间覆盖")
        return FxRate(currency, target, sum(rows[day] for day in days) / len(days), days)
    except Exception as error:  # noqa: BLE001 — 网络/缓存边界，返回明确失败原因
        logger.warning("期间汇率获取失败 %s/%s %s..%s: %s", currency, target, start, end, error)
        return FxRate(currency, target, reason=f"{type(error).__name__}: {error}")

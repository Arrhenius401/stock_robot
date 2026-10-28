"""黄金专用结果策略：验证来源事实，区分历史覆盖和当前价格。"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from itertools import pairwise

from data.schemas import ResultState
from gold.models import GoldBar, GoldObservation, GoldRules, GoldSeries
from gold.sources import SOURCE_INFO


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def _dates(series: GoldSeries) -> list[date]:
    if series.key in ("domestic", "international"):
        return sorted(bar.trade_date for bar in series.bars)
    return sorted(point.observation_date for point in series.observations)


def _finite(value: object, *, positive: bool = False) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and (not positive or value > 0)
    )


def _valid(series: GoldSeries, now: datetime) -> bool:
    info = SOURCE_INFO.get(series.key)
    if (
        info is None
        or series.key == "global_reserves"
        or series.status != "ok"
        or series.unit != info[3]
        or series.source != info[1]
        or series.frequency != ("monthly" if series.key == "china_reserves" else "daily")
        or series.fetched_at.utcoffset() is None
        or series.fetched_at > now
    ):
        return False
    if series.published_at is not None:
        if series.published_at.utcoffset() is None:
            return False
        if series.metadata.get("published_precision") == "date":
            if series.published_at.date() > now.astimezone(series.published_at.tzinfo).date():
                return False
        elif series.published_at > now:
            return False

    price = series.key in ("domestic", "international")
    if price:
        if not series.bars or series.observations:
            return False
        for bar in series.bars:
            if not isinstance(bar, GoldBar):
                return False
            values = (bar.open, bar.high, bar.low, bar.close)
            if not all(_finite(value, positive=True) for value in values):
                return False
            if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
                return False
    else:
        if not series.observations or series.bars:
            return False
        for point in series.observations:
            if not isinstance(point, GoldObservation) or not _finite(point.value, positive=series.key != "real_yield"):
                return False

    days = _dates(series)
    if len(set(days)) != len(days) or any(day > now.date() for day in days):
        return False
    if series.key == "china_reserves":
        # 官方储备是月末数量；同月多条或非月末不可用于重建月度序列。
        months = [(day.year, day.month) for day in days]
        if len(set(months)) != len(months) or any((day + timedelta(days=1)).month == day.month for day in days):
            return False
    return True


def _covers_request(series: GoldSeries, days: list[date], now: datetime, rules: GoldRules) -> bool:
    if series.key == "china_reserves":
        if len(days) <= rules.reserve_main_months or days[0].year > now.year - 1 or days[0].month != 1:
            return False
        return all(b.year * 12 + b.month - (a.year * 12 + a.month) == 1 for a, b in pairwise(days))

    cutoff = _years_before(days[-1], rules.lookback_years)
    eligible = [day for day in days if day >= cutoff]
    if days[0] > cutoff:
        return False
    if series.key in ("domestic", "international", "fx"):
        return len(eligible) >= rules.min_price_5y_samples

    # 变化分位既需要五年样本，也需要最早五年样本之前的主窗口预热。
    first_eligible = next(i for i, day in enumerate(days) if day >= cutoff)
    return first_eligible >= rules.main_window and len(eligible) - 1 >= rules.min_change_rank_samples


def _request_cutoff(series: GoldSeries, now: datetime) -> date:
    if series.key == "china_reserves":
        return date(now.year - 1, 1, 1)
    # 来源当前请求五年加30天；变化分位来源再向前取220天用于预热。
    warmup = 220 if series.key in ("real_yield", "dollar", "holdings") else 0
    return _years_before(now.date(), 5) - timedelta(days=30 + warmup)


def classify_gold(
    series: GoldSeries, previous: GoldSeries | None, now: datetime, rules: GoldRules
) -> ResultState:
    """按来源请求窗口分类；部分数据仍可展示实际取得的价格。"""
    if now.utcoffset() is None or not _valid(series, now):
        return "failed"
    days = _dates(series)
    max_age = (
        rules.reserves_max_age_days
        if series.key == "china_reserves"
        else rules.fx_max_age_days
        if series.key in ("dollar", "fx")
        else rules.daily_max_age_days
    )
    if (now.date() - days[-1]).days > max_age or not _covers_request(series, days, now, rules):
        return "partial"
    if previous is not None and previous.key == series.key and _valid(previous, now):
        previous_days = _dates(previous)
        cutoff = _request_cutoff(series, now)
        required = {day for day in previous_days if day >= cutoff}
        if days[-1] < previous_days[-1] or not required.issubset(days):
            return "partial"
    return "complete"

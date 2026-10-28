"""黄金市场观察：计算价格与可追溯的描述性事实。"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from itertools import pairwise
from statistics import mean, stdev
from typing import Any

from gold.models import (
    GoldBar,
    GoldCollection,
    GoldObservation,
    GoldRules,
    GoldSeries,
)


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def _midrank(values: list[float], current: float) -> float | None:
    if not values:
        return None
    return (
        100
        * (sum(v < current for v in values) + 0.5 * sum(v == current for v in values))
        / len(values)
    )


def _clean(
    series: GoldSeries | None, today: date, *, bars: bool = False
) -> tuple[list[Any], list[str]]:
    if series is None:
        return [], ["来源缺失"]
    raw = series.bars if bars else series.observations
    by_date: dict[date, Any] = {}
    notes: list[str] = []
    for point in raw:
        day = point.trade_date if isinstance(point, GoldBar) else point.observation_date
        if day > today:
            notes.append("未来日期已排除")
            continue
        if day in by_date and point != by_date[day]:
            return [], ["同日冲突记录，不能选择性取值"]
        by_date[day] = point
    return [by_date[d] for d in sorted(by_date)], notes


def _change(points: list[GoldObservation], window: int, key: str) -> float | None:
    if len(points) <= window:
        return None
    start, end = points[-window - 1].value, points[-1].value
    if key == "real_yield":
        return (end - start) * 100
    return (end / start - 1) * 100 if start > 0 and end > 0 else None


def _change_rank(
    points: list[GoldObservation], window: int, key: str, rules: GoldRules
) -> dict:
    result: dict[str, Any] = {
        "percentile": None,
        "percentile_status": "五年变化分布及预热样本不足",
        "rank_start": None,
        "rank_end": None,
        "rank_count": 0,
    }
    current = _change(points, window, key)
    if current is None or not points:
        return result
    cutoff = _years_before(points[-1].observation_date, rules.lookback_years)
    past = []
    dates = []
    for i in range(window, len(points) - 1):
        if points[i].observation_date >= cutoff:
            value = _change(points[i - window : i + 1], window, key)
            if value is not None:
                past.append(-value if key in ("real_yield", "dollar") else value)
                dates.append(points[i].observation_date)
    result["rank_count"] = len(past)
    if dates:
        result.update(rank_start=dates[0].isoformat(), rank_end=dates[-1].isoformat())
    # 五年前的比较窗口也必须具备同长度预热，不能用五年原值冒充。
    first_eligible = next(
        (i for i, p in enumerate(points) if p.observation_date >= cutoff), len(points)
    )
    if (
        points[0].observation_date <= cutoff
        and first_eligible >= window
        and len(past) >= rules.min_change_rank_samples
    ):
        current = -current if key in ("real_yield", "dollar") else current
        result.update(
            percentile=_midrank(past, current), percentile_status="五年变化分位"
        )
    return result


def _empty_window(window: int, unit: str) -> dict:
    return {
        "window": window,
        "unit": unit,
        "change": None,
        "start": None,
        "end": None,
        "count": 0,
        "rank_start": None,
        "rank_end": None,
        "rank_count": 0,
        "percentile": None,
        "percentile_status": "数据不足",
    }


def _monthly_window(points: list[GoldObservation], months: int) -> dict:
    result = _empty_window(months, "数量变化")
    if len(points) <= months:
        return result
    sample = points[-months - 1 :]
    ordinals = [p.observation_date.year * 12 + p.observation_date.month for p in sample]
    if any(b - a != 1 for a, b in pairwise(ordinals)):
        return result
    # 必须为月末点；缺月不能通过更早的观察值补足。
    for point in sample:
        day = point.observation_date
        if (day + timedelta(days=1)).month == day.month:
            return result
    change = sample[-1].value - sample[0].value
    result.update(
        change=change,
        start=sample[0].observation_date.isoformat(),
        end=sample[-1].observation_date.isoformat(),
        count=len(sample),
        percentile_status="月度数量不计算日频变化分位",
    )
    return result


def _future_publication(series: GoldSeries, now: datetime) -> bool:
    if series.published_at is None:
        return False
    if series.metadata.get("published_precision") == "date":
        return (
            series.published_at.date()
            > now.astimezone(series.published_at.tzinfo).date()
        )
    return series.published_at > now


def _factor(
    key: str, series: GoldSeries | None, now: datetime, rules: GoldRules
) -> dict:
    today = now.date()
    points, issues = _clean(series, today)
    monthly = key in ("global_reserves", "china_reserves")
    limit = (
        rules.reserves_max_age_days
        if monthly
        else rules.fx_max_age_days
        if key == "dollar"
        else rules.daily_max_age_days
    )
    valid = bool(points) and (today - points[-1].observation_date).days <= limit
    if series is None or series.status != "ok":
        valid = False
    if series and _future_publication(series, now):
        valid = False
        issues.append("记录的发布日期在报告日期之后")
    if series and series.status == "stale":
        issues.append("刷新失败，旧缓存仅供历史参考，不参与本次观察窗口计算")
    if not valid:
        issues.append("观察日过期或数据不可用")
    if key != "real_yield" and any(p.value <= 0 for p in points):
        valid = False
        issues.append("数量或指数必须为正数")
    result = {
        "key": key,
        "name": series.name if series else key,
        "source": series.source if series else None,
        "source_url": series.source_url if series else None,
        "unit": series.unit if series else None,
        "frequency": series.frequency
        if series
        else ("monthly" if monthly else "daily"),
        "as_of": points[-1].observation_date.isoformat() if points else None,
        "latest_value": points[-1].value if points else None,
        "published_at": series.published_at.isoformat()
        if series and series.published_at
        else None,
        "published_precision": series.metadata.get("published_precision")
        if series
        else None,
        "state": "有效" if valid else "数据缺失",
        "source_status": series.status if series else "unavailable",
        "result_state": series.collection_record.result_state if series and series.collection_record else None,
        "error": series.error if series else None,
        "notes": issues + (series.notes if series else []),
    }
    for label, window, months in [
        ("main", rules.main_window, rules.reserve_main_months),
        ("marginal", rules.marginal_window, rules.reserve_marginal_months),
    ]:
        unit = "bp" if key == "real_yield" else "%"
        part = _empty_window(
            months if monthly else window, series.unit if monthly and series else unit
        )
        if valid and monthly:
            part = _monthly_window(points, months)
            part["unit"] = series.unit if series else "数量"
        elif valid:
            change = _change(points, window, key)
            if change is not None:
                part.update(
                    change=change,
                    start=points[-window - 1].observation_date.isoformat(),
                    end=points[-1].observation_date.isoformat(),
                    count=window + 1,
                )
                part.update(_change_rank(points, window, key, rules))
        result[label] = part
    if monthly:
        result["notes"].append("滞后指标，仅反映长期背景；单一国家不能代表全球")
    if valid and any(result[label]["change"] is None for label in ("main", "marginal")):
        result["state"] = "数据不足"
        result["notes"].append("部分观察窗口样本不足或月份不连续")
    return result


def _observations(collection: GoldCollection, rules: GoldRules) -> list[dict]:
    return [
        _factor(key, collection.series.get(key), collection.generated_at, rules)
        for key in (
            "real_yield",
            "dollar",
            "global_reserves",
            "china_reserves",
            "holdings",
        )
    ]


def _wilder_rsi(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    differences = [b - a for a, b in pairwise(values)]
    gain = mean(max(d, 0) for d in differences[:period])
    loss = mean(max(-d, 0) for d in differences[:period])
    for difference in differences[period:]:
        gain = (gain * (period - 1) + max(difference, 0)) / period
        loss = (loss * (period - 1) + max(-difference, 0)) / period
    return (
        50.0
        if gain == loss == 0
        else 100.0
        if loss == 0
        else 100 - 100 / (1 + gain / loss)
    )


def _wilder_atr(bars: list[GoldBar], period: int) -> float | None:
    if len(bars) <= period:
        return None
    ranges = [
        max(
            bar.high - bar.low,
            abs(bar.high - previous.close),
            abs(bar.low - previous.close),
        )
        for previous, bar in pairwise(bars)
    ]
    atr = mean(ranges[:period])
    for value in ranges[period:]:
        atr = (atr * (period - 1) + value) / period
    return atr


def _short_direction(
    close: float, fast: float, slow: float, slope: float, rules: GoldRules
) -> str:
    if close >= fast >= slow and slope > rules.flat_slope_pct:
        return "看多（强）"
    if close <= fast <= slow and slope < -rules.flat_slope_pct:
        return "看空（强）"
    if close > fast and slope >= -rules.flat_slope_pct:
        return "偏多"
    if close < fast and slope <= rules.flat_slope_pct:
        return "偏空"
    return "中性"


def _slope(values: list[float], rules: GoldRules, offset: int = 0) -> float | None:
    points = values[:-offset] if offset else values
    if len(points) < rules.ma_slow + rules.ma_slope_window:
        return None
    return (
        mean(points[-rules.ma_slow :])
        / mean(points[-rules.ma_slow - rules.ma_slope_window : -rules.ma_slope_window])
        - 1
    ) * 100


def _price_position(bars: list[GoldBar], rules: GoldRules) -> dict:
    result = {}
    for years, minimum in [
        (3, rules.min_price_3y_samples),
        (5, rules.min_price_5y_samples),
    ]:
        cutoff = _years_before(bars[-1].trade_date, years) if bars else None
        sample = [b for b in bars if cutoff is not None and b.trade_date >= cutoff]
        valid = (
            bool(bars)
            and cutoff is not None
            and bars[0].trade_date <= cutoff
            and len(sample) >= minimum
        )
        result[f"{years}y"] = {
            "percentile": _midrank([b.close for b in sample], bars[-1].close)
            if valid
            else None,
            "start": sample[0].trade_date.isoformat() if sample else None,
            "end": sample[-1].trade_date.isoformat() if sample else None,
            "count": len(sample),
            "status": "有效" if valid else "完整期限或样本不足",
        }
    return result


def _volatility(bars: list[GoldBar], rules: GoldRules) -> dict:
    values = [b.close for b in bars]
    returns = [b / a - 1 for a, b in pairwise(values)]
    annual = math.sqrt(rules.annual_days) * 100
    vol = (
        stdev(returns[-rules.vol_window :]) * annual
        if len(returns) >= rules.vol_window
        else None
    )
    cutoff = (
        _years_before(bars[-1].trade_date, rules.lookback_years) if bars else date.min
    )
    past = []
    dates = []
    for end in range(rules.vol_window, len(returns)):
        if bars[end].trade_date >= cutoff:
            past.append(stdev(returns[end - rules.vol_window : end]) * annual)
            dates.append(bars[end].trade_date)
    rank = (
        _midrank(past, vol)
        if vol is not None and len(past) >= rules.min_vol_rank_samples
        else None
    )
    sample_returns = returns[-rules.annual_days :]
    sample_prices = values[-rules.annual_days - 1 :]
    peak = sample_prices[0] if sample_prices else 1.0
    drawdown = 0.0
    for value in sample_prices:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return {
        "vol20_pct": vol,
        "vol_percentile": rank,
        "vol_rank_start": dates[0].isoformat() if dates else None,
        "vol_rank_end": dates[-1].isoformat() if dates else None,
        "vol_rank_count": len(past),
        "vol_rank_period": "五年波动分位"
        if bars
        and bars[0].trade_date <= cutoff
        and len(past) >= rules.min_vol_rank_samples
        else "实际样本期间波动分位",
        "max_drawdown_pct": drawdown * 100
        if len(returns) >= rules.annual_days
        else None,
        "annual_vol_pct": stdev(sample_returns) * annual
        if len(returns) >= rules.annual_days
        else None,
    }


def _market(
    key: str, series: GoldSeries | None, collection: GoldCollection, rules: GoldRules
) -> dict:
    bars, issues = _clean(series, collection.generated_at.date(), bars=True)
    observed_values = [bar.close for bar in bars]
    fresh = (
        bool(bars)
        and (collection.generated_at.date() - bars[-1].trade_date).days
        <= rules.daily_max_age_days
        and series is not None
        and series.status == "ok"
        and not _future_publication(series, collection.generated_at)
    )
    # 缓存历史价格可展示，但过期或刷新失败的序列不进入本次技术计算。
    current_bars = bars if fresh else []
    values = observed_values if fresh else []
    fast = mean(values[-rules.ma_fast :]) if len(values) >= rules.ma_fast else None
    slow = mean(values[-rules.ma_slow :]) if len(values) >= rules.ma_slow else None
    slope = _slope(values, rules)
    direction = (
        _short_direction(values[-1], fast, slow, slope, rules)
        if fresh and fast is not None and slow is not None and slope is not None
        else "无法判断"
    )
    if direction == "无法判断":
        reason = f"行情缺失、过期或有效收盘样本不足（至少需要{rules.ma_slow + rules.ma_slope_window}个观察日）。"
    else:
        assert fast is not None and slow is not None and slope is not None

        def relation(a: float, b: float) -> str:
            return "高于" if a > b else "低于" if a < b else "等于"

        slope_text = "上涨" if slope > 0 else "下降" if slope < 0 else "持平"
        reason = f"收盘价{relation(values[-1], fast)}{rules.ma_fast}日均线，{relation(values[-1], slow)}{rules.ma_slow}日均线；{rules.ma_slow}日均线近{rules.ma_slope_window}个观察日{slope_text}（{slope:+.2f}%）。"
    rsi = _wilder_rsi(values, rules.rsi_period)
    atr = _wilder_atr(current_bars, rules.atr_period)
    deviation = (
        (values[-1] - fast) / atr
        if values and fast is not None and atr is not None and atr > 0
        else None
    )
    indicators = {
        "ma20": fast,
        "ma60": slow,
        "slope_pct": slope,
        "prior_slope_pct": _slope(values, rules, 5),
        "rsi14": rsi,
        "atr14": atr,
        "deviation_atr": deviation,
        **_volatility(current_bars, rules),
    }
    position = _price_position(current_bars, rules)
    vol_rank = indicators["vol_percentile"]
    ranks = {key: value["percentile"] for key, value in position.items()}
    complete = (
        fresh
        and rsi is not None
        and atr is not None
        and vol_rank is not None
        and all(value is not None for value in ranks.values())
    )
    confidence = (
        "低"
        if not complete or direction == "无法判断"
        else "中"
        if series and (series.status == "stale" or issues)
        else "高"
    )
    reasons = list(issues)
    if not fresh:
        reasons.append("行情缺失、过期或发布日期晚于报告时点")
    if not complete:
        reasons.append("部分技术指标或历史位置样本不足")
    if series and series.status == "stale":
        reasons.append("来源刷新失败，沿用缓存")
    if not reasons:
        reasons.append("行情与指标观察窗口完整")
    risks = [
        {
            "key": "oversold",
            "message": "RSI处于超卖观察区间；技术状态未经择时效果验证。",
            "value": rsi,
            "threshold": rules.rsi_oversold,
            "active": rsi <= rules.rsi_oversold if fresh and rsi is not None else None,
        },
        {
            "key": "high_volatility",
            "message": "近期波动率处于历史样本较高位置。",
            "value": vol_rank,
            "threshold": rules.vol_high_percentile,
            "active": vol_rank >= rules.vol_high_percentile
            if fresh and vol_rank is not None
            else None,
        },
        {
            "key": "historical_high",
            "message": "价格位于历史样本较高位置；不代表高估或低估。",
            "value": ranks,
            "threshold": rules.price_high_percentile,
            "active": any(
                value >= rules.price_high_percentile
                for value in ranks.values()
                if value is not None
            )
            if fresh and all(value is not None for value in ranks.values())
            else None,
        },
        {
            "key": "risk_data_missing",
            "message": "部分风险观察数据不足。",
            "value": None,
            "threshold": None,
            "active": not complete if fresh else None,
        },
    ]
    performance = {}
    for window in (5, 20, 60, 120, 250):
        valid = len(values) > window
        performance[str(window)] = {
            "return_pct": (values[-1] / values[-window - 1] - 1) * 100
            if valid
            else None,
            "start": bars[-window - 1].trade_date.isoformat() if valid else None,
            "end": bars[-1].trade_date.isoformat() if bars else None,
            "count": window + 1 if valid else len(bars),
            "status": "有效"
            if valid
            else "数据过期"
            if bars and not fresh
            else "样本不足",
        }
    return {
        "name": series.name
        if series
        else ("国内黄金" if key == "domestic" else "国际黄金"),
        "symbol": "Au99.99" if key == "domestic" else "XAU",
        "unit": series.unit
        if series
        else ("元/克" if key == "domestic" else "美元/金衡盎司"),
        "source": series.source if series else None,
        "source_url": series.source_url if series else None,
        "status": ("unavailable" if series is None or series.status == "unavailable"
                   else "ok" if fresh else "stale" if bars else "unavailable"),
        "source_status": series.status if series else "unavailable",
        "result_state": series.collection_record.result_state if series and series.collection_record else None,
        "error": series.error if series else None,
        "notes": issues + (series.notes if series else []),
        "as_of": bars[-1].trade_date.isoformat() if bars and series and series.status != "unavailable" else None,
        "latest_close": observed_values[-1] if observed_values and series and series.status != "unavailable" else None,
        "daily_change": observed_values[-1] - observed_values[-2]
        if len(observed_values) > 1 and series and series.status != "unavailable" else None,
        "daily_change_pct": (observed_values[-1] / observed_values[-2] - 1) * 100
        if len(observed_values) > 1 and series and series.status != "unavailable"
        else None,
        "performance": performance,
        "short_term": {
            "direction": direction,
            "reason": reason,
            "confidence": confidence,
            "confidence_reasons": reasons,
        },
        "indicators": indicators,
        "price_position": position,
        "risks": risks,
    }


def _fx_decomposition(collection: GoldCollection, rules: GoldRules) -> list[dict]:
    domestic, international, fx = (
        collection.series.get(k) for k in ("domestic", "international", "fx")
    )
    if domestic is None or international is None or fx is None:
        return []
    domestic_units = {"元/克", "人民币元/克", "人民币/克", "CNY/g"}
    international_units = {"美元/金衡盎司", "美元/盎司", "USD/oz"}
    fx_units = {"人民币/美元", "人民币元/美元", "CNY/USD", "元/美元"}
    if (
        domestic.unit not in domestic_units
        or international.unit not in international_units
        or fx.unit not in fx_units
    ):
        return []
    today = collection.generated_at.date()
    d, _ = _clean(domestic, today, bars=True)
    g, _ = _clean(international, today, bars=True)
    f, _ = _clean(fx, today)
    if (
        not d
        or not g
        or not f
        or any(s.status != "ok" for s in (domestic, international, fx))
        or any(
            _future_publication(s, collection.generated_at)
            for s in (domestic, international, fx)
        )
    ):
        return []
    if (today - f[-1].observation_date).days > rules.fx_max_age_days:
        return []
    if any(
        (today - points[-1].trade_date).days > rules.daily_max_age_days
        for points in (d, g)
    ):
        return []
    dmap, gmap, fmap = (
        {b.trade_date: b.close for b in d},
        {b.trade_date: b.close for b in g},
        {p.observation_date: p.value for p in f},
    )
    days = sorted(dmap.keys() & gmap.keys() & fmap.keys())
    if not days or (today - days[-1]).days > min(rules.daily_max_age_days, rules.fx_max_age_days):
        return []
    result = []
    for window in (20, 60):
        if len(days) <= window:
            continue
        start, end = days[-window - 1], days[-1]
        if min(dmap[start], gmap[start], fmap[start], fmap[end]) <= 0:
            continue
        dr, gr, fr = (
            dmap[end] / dmap[start] - 1,
            gmap[end] / gmap[start] - 1,
            fmap[end] / fmap[start] - 1,
        )
        interaction = gr * fr
        reference = gr + fr + interaction
        result.append(
            {
                "window": window,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "count": window + 1,
                "international_return_pct": gr * 100,
                "fx_return_pct": fr * 100,
                "domestic_return_pct": dr * 100,
                "international_contribution_pp": (gr + interaction / 2) * 100,
                "fx_contribution_pp": (fr + interaction / 2) * 100,
                "interaction_pp": interaction * 100,
                "reference_return_pct": reference * 100,
                "residual_pp": (dr - reference) * 100,
                "note": "非同步日线参考比较；未解释差额包含时点、报价和基差等影响，不是因果归因或可交易溢价",
            }
        )
    return result


def analyze_collection(
    collection: GoldCollection, rules: GoldRules | None = None
) -> dict:
    """以当前事实生成可归档的 JSON 兼容观察结果。"""
    rules = rules or GoldRules()
    return {
        "observations": _observations(collection, rules),
        "markets": {
            key: _market(key, collection.series.get(key), collection, rules)
            for key in ("domestic", "international")
        },
        "fx_decomposition": _fx_decomposition(collection, rules),
    }

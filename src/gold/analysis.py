"""黄金确定性研究规则：方向、风险和操作指引分别计算。"""

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
        "signal": "数据缺失",
        "score": None,
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
    score = 1 if change > 0 else -1 if change < 0 else 0
    result.update(
        change=change,
        start=sample[0].observation_date.isoformat(),
        end=sample[-1].observation_date.isoformat(),
        count=len(sample),
        signal={1: "利多", 0: "中性", -1: "利空"}[score],
        score=score,
        percentile_status="月度数量不计算日频变化分位",
    )
    return result


def _future_publication(series: GoldSeries, now: datetime) -> bool:
    if series.published_at is None:
        return False
    if series.metadata.get("published_precision") == "date":
        return series.published_at.date() > now.astimezone(series.published_at.tzinfo).date()
    return series.published_at > now


def _factor(key: str, series: GoldSeries | None, now: datetime, rules: GoldRules) -> dict:
    today = now.date()
    points, issues = _clean(series, today)
    monthly = key in ("global_reserves", "china_reserves")
    age_limit = (
        rules.reserves_max_age_days
        if monthly
        else rules.fx_max_age_days
        if key == "dollar"
        else rules.daily_max_age_days
    )
    valid = bool(points) and (today - points[-1].observation_date).days <= age_limit
    if series is None or series.status == "unavailable":
        valid = False
    if series and _future_publication(series, now):
        valid = False
        issues.append("记录的发布日期在报告日期之后")
    if series and series.status == "stale":
        issues.append("刷新失败，沿用原始观察日仍有效的旧缓存")
    if not valid:
        issues.append("观察日过期或数据不可用")
    if key != "real_yield" and any(p.value <= 0 for p in points):
        valid = False
        issues.append("数量或指数必须为正数")
    result = {
        "key": key,
        "name": series.name if series else key,
        "role": "核心" if key in ("real_yield", "dollar") else "参考",
        "weight": rules.core_weight
        if key in ("real_yield", "dollar")
        else rules.reference_weight,
        "source": series.source if series else None,
        "source_url": series.source_url if series else None,
        "unit": series.unit if series else None,
        "as_of": points[-1].observation_date.isoformat() if points else None,
        "published_at": series.published_at.isoformat()
        if series and series.published_at
        else None,
        "state": "有效" if valid else "数据缺失",
        "source_status": series.status if series else "unavailable",
        "published_precision": series.metadata.get("published_precision") if series else None,
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
        if valid:
            if monthly:
                part = _monthly_window(points, months)
                part["unit"] = series.unit if series else "数量"
            else:
                change = _change(points, window, key)
                if change is not None:
                    threshold = (
                        rules.rate_threshold_bp
                        if key == "real_yield"
                        else rules.dollar_threshold_pct
                        if key == "dollar"
                        else rules.holdings_threshold_pct
                    )
                    if label == "marginal":
                        override = getattr(
                            rules,
                            {
                                "real_yield": "rate_marginal_threshold_bp",
                                "dollar": "dollar_marginal_threshold_pct",
                                "holdings": "holdings_marginal_threshold_pct",
                            }[key],
                            None,
                        )
                        threshold = override if override is not None else threshold
                    oriented = -change if key in ("real_yield", "dollar") else change
                    score = (
                        1
                        if oriented >= threshold
                        else -1
                        if oriented <= -threshold
                        else 0
                    )
                    part.update(
                        change=change,
                        start=points[-window - 1].observation_date.isoformat(),
                        end=points[-1].observation_date.isoformat(),
                        count=window + 1,
                        score=score,
                        signal={1: "利多", 0: "中性", -1: "利空"}[score],
                        threshold=threshold,
                    )
                    part.update(_change_rank(points, window, key, rules))
                    rank = part["percentile"]
                    part["significance"] = (
                        "显著利多"
                        if score == 1
                        and rank is not None
                        and rank >= getattr(rules, "significant_high_percentile", 80)
                        else "显著利空"
                        if score == -1
                        and rank is not None
                        and rank <= getattr(rules, "significant_low_percentile", 20)
                        else part["signal"]
                    )
        result[label] = part
    if monthly:
        result["notes"].append("滞后指标，仅用于长期趋势参考")
    if result["main"]["score"] is None or result["marginal"]["score"] is None:
        result["state"] = "数据缺失"
    return result


def _long_term(collection: GoldCollection, rules: GoldRules) -> dict:
    factors = [
        _factor(key, collection.series.get(key), collection.generated_at, rules)
        for key in (
            "real_yield",
            "dollar",
            "global_reserves",
            "china_reserves",
            "holdings",
        )
    ]
    global_factor, china = factors[2:4]
    selected = global_factor if global_factor["state"] == "有效" else china
    (china if selected is global_factor else global_factor)["weight"] = 0
    if selected is china:
        china["notes"].append("仅中国样本，不能代表全球官方需求")
    core_valid = all(f["state"] == "有效" for f in factors[:2])
    windows = {}
    for label, window in [
        ("main", rules.main_window),
        ("marginal", rules.marginal_window),
    ]:
        core = sum(f["weight"] * (f[label]["score"] or 0) for f in factors[:2])
        reference = sum(f["weight"] * (f[label]["score"] or 0) for f in factors[2:])
        total = core + reference
        direction = (
            "无法判断"
            if not core_valid
            else "看多"
            if core > 0 and total > 0
            else "看空"
            if core < 0 and total < 0
            else "中性"
        )
        windows[label] = {
            "window": window,
            "direction": direction,
            "core_score": core,
            "reference_score": reference,
            "total_score": total,
        }
    main, marginal = windows["main"]["direction"], windows["marginal"]["direction"]
    direction = "无法判断" if not core_valid else main if main == marginal else "中性"
    references = sum(f["state"] == "有效" and f["weight"] > 0 for f in factors[2:])
    history_ok = all(
        f[label]["percentile"] is not None
        for f in (factors[0], factors[1], factors[4])
        for label in ("main", "marginal")
    )
    reasons = []
    if not core_valid:
        confidence = "低"
        reasons.append("必需核心或双窗口缺失，不能形成长期方向")
    elif references == 0:
        confidence = "低"
        reasons.append("需求端参考全部缺失，当前仅由核心宏观证据支撑")
    elif references == 2 and selected is global_factor and history_ok:
        confidence = "高"
        reasons.append("核心双窗口、全球官方储备、持仓样本及历史变化分布完整")
    else:
        confidence = "中"
        if selected is china and china["state"] == "有效":
            reasons.append("官方储备降级为中国地区样本")
        if references < 2:
            reasons.append("部分需求参考缺失，需求验证不完整")
        if not history_ok:
            reasons.append("五年变化分布或预热样本不足")
    if any(f["source_status"] == "stale" for f in factors if f["state"] == "有效"):
        if confidence == "高":
            confidence = "中"
        reasons.append("来源刷新失败，旧缓存按原观察日期使用，不视为刚发布")
    if any("未来日期已排除" in f["notes"] for f in factors):
        if confidence == "高":
            confidence = "中"
        reasons.append("来源包含已排除的未来日期，需继续核对解析口径")
    reason = (
        "核心缺失或过期"
        if not core_valid
        else "主趋势与边际窗口同向"
        if main == marginal
        else f"主趋势{main}、边际{marginal}，双窗口未同向"
    )
    return {
        "direction": direction,
        "confidence": confidence,
        "confidence_reasons": reasons,
        "reason": reason,
        "windows": windows,
        "factors": factors,
        "missing": [
            f["name"]
            for f in factors
            if f["state"] != "有效"
            and f["key"]
            != ("china_reserves" if selected is global_factor else "global_reserves")
        ],
    }


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
    key: str,
    series: GoldSeries | None,
    collection: GoldCollection,
    long_term: dict,
    rules: GoldRules,
) -> dict:
    bars, issues = _clean(series, collection.generated_at.date(), bars=True)
    values = [b.close for b in bars]
    fresh = (
        bool(bars)
        and (collection.generated_at.date() - bars[-1].trade_date).days
        <= rules.daily_max_age_days
        and series is not None
        and series.status != "unavailable"
        and not _future_publication(series, collection.generated_at)
    )
    slope = _slope(values, rules)
    fast = mean(values[-rules.ma_fast :]) if len(values) >= rules.ma_fast else None
    slow = mean(values[-rules.ma_slow :]) if len(values) >= rules.ma_slow else None
    direction = (
        _short_direction(values[-1], fast, slow, slope, rules)
        if fresh and fast is not None and slow is not None and slope is not None
        else "无法判断"
    )
    rsi = _wilder_rsi(values, rules.rsi_period)
    atr = _wilder_atr(bars, rules.atr_period)
    deviation = (
        (values[-1] - fast) / atr
        if values and fast is not None and atr is not None and atr > 0
        else None
    )
    indicators = {
        "ma20": fast,
        "ma60": slow,
        "slope_pct": slope,
        "rsi14": rsi,
        "atr14": atr,
        "deviation_atr": deviation,
        **_volatility(bars, rules),
    }
    position = _price_position(bars, rules)
    high_positions = {
        k: v["percentile"]
        for k, v in position.items()
        if v["percentile"] is not None
        and v["percentile"] >= rules.price_high_percentile
    }
    vol_rank = indicators["vol_percentile"]
    hot = (rsi is not None and rsi >= rules.rsi_hot) or (
        deviation is not None and deviation >= rules.atr_hot
    )
    # 没有前次状态时保守要求明确退出；只低于触发线并不解除新增限制。
    hot_exit = (
        rsi is not None
        and rsi <= rules.rsi_exit
        and deviation is not None
        and abs(deviation) <= rules.atr_exit
    )
    previous_hot = series.metadata.get("overheated_active") if series else None
    heat_block = hot or ((previous_hot is not False) and not hot_exit)
    vol_high = vol_rank is not None and vol_rank >= rules.vol_high_percentile
    risk_complete = (
        fresh
        and rsi is not None
        and atr is not None
        and atr > 0
        and vol_rank is not None
        and all(p["percentile"] is not None for p in position.values())
    )
    reasons = list(issues)
    if not fresh:
        reasons.append("本市场行情缺失或观察日超过门槛")
    if not risk_complete:
        reasons.append("关键风险或三年/五年历史位置样本不足，不给积极开仓参考")
    confidence = "低" if direction == "无法判断" or not risk_complete else "高"
    if series and (series.status == "stale" or issues):
        confidence = "低" if not risk_complete else "中"
        reasons.append("来源刷新失败或解析存在排除项，保留实际观察日期")
    if confidence == "高":
        reasons.append("本市场方向、风险及三年/五年窗口完整且新鲜")
    vol_message = (
        "上涨波动大，追高后回调风险需重点关注"
        if direction in ("看多（强）", "偏多")
        else "下跌波动大，抄底风险高"
        if direction in ("看空（强）", "偏空")
        else "方向不明且波动大，不建议主动开仓"
    )
    risks = [
        {
            "key": "overheated",
            "message": "追高风险高"
            if hot
            else "前次追高警报尚未达到解除条件"
            if heat_block and previous_hot is True
            else "前次过热状态未知，暂未通过保守解除门槛"
            if heat_block
            else "追高警报已解除",
            "value": {"rsi14": rsi, "deviation_atr": deviation},
            "threshold": {"rsi": rules.rsi_hot, "atr": rules.atr_hot},
            "active": heat_block,
        },
        {
            "key": "high_volatility",
            "message": vol_message,
            "value": vol_rank,
            "threshold": rules.vol_high_percentile,
            "active": vol_high,
        },
        {
            "key": "historical_high",
            "message": "价格处于近三年/五年历史高位，追高需谨慎",
            "value": high_positions,
            "threshold": rules.price_high_percentile,
            "active": bool(high_positions),
        },
        {
            "key": "oversold",
            "message": "短期超卖，存在技术性反弹可能；不改变方向或构成买入理由",
            "value": rsi,
            "threshold": rules.rsi_oversold,
            "active": rsi is not None and rsi <= rules.rsi_oversold,
        },
        {
            "key": "risk_data_missing",
            "message": "关键风险或历史样本不足",
            "value": risk_complete,
            "threshold": True,
            "active": not risk_complete,
        },
    ]
    prior_slope = _slope(values, rules, 5)
    reassessment = [
        {
            "key": "heat_exit",
            "description": "RSI与价格偏离同时通过退出带后，重新复核强看多",
            "value": {
                "rsi14": rsi,
                "abs_deviation_atr": abs(deviation) if deviation is not None else None,
            },
            "threshold": {"rsi_lte": rules.rsi_exit, "atr_lte": rules.atr_exit},
            "met": hot_exit,
        },
        {
            "key": "vol_exit",
            "description": "波动分位降至门槛以下，方向仍须单独满足",
            "value": vol_rank,
            "threshold": {"lt": rules.vol_high_percentile},
            "met": vol_rank is not None and not vol_high,
        },
        {
            "key": "position_exit",
            "description": "所有三年/五年有效分位均低于高位门槛，并补齐历史样本",
            "value": {k: v["percentile"] for k, v in position.items()},
            "threshold": {"lt": rules.price_high_percentile},
            "met": all(
                p["percentile"] is not None
                and p["percentile"] < rules.price_high_percentile
                for p in position.values()
            ),
        },
        {
            "key": "downtrend_improvement",
            "description": "收盘高于MA20且S(t)>S(t−5)，仅表示下跌趋势初步改善",
            "value": {
                "close": values[-1] if values else None,
                "ma20": fast,
                "slope_pct": slope,
                "prior_slope_pct": prior_slope,
            },
            "threshold": "close>MA20 且 S(t)>S(t−5)",
            "met": bool(
                values
                and fast is not None
                and slope is not None
                and prior_slope is not None
                and values[-1] > fast
                and slope > prior_slope
            ),
        },
        {
            "key": "strong_bull",
            "description": "方向由弱转强需完整满足强看多规则",
            "value": direction,
            "threshold": f"收盘≥MA20≥MA60 且 S>{rules.flat_slope_pct}%",
            "met": direction == "看多（强）",
        },
        {
            "key": "long_windows",
            "description": "核心有效、双窗口重新同向，且参考未削弱至中性",
            "value": long_term["windows"],
            "threshold": f"{rules.main_window}/{rules.marginal_window}日窗口均看多",
            "met": long_term["direction"] == "看多",
        },
        {
            "key": "fresh_data",
            "description": "取得通过口径检查且满足观察日门槛的数据；刷新缓存时间不算解除",
            "value": bars[-1].trade_date.isoformat() if bars else None,
            "threshold": {"max_age_days": rules.daily_max_age_days},
            "met": fresh,
        },
    ]
    entry_rsi = rsi is not None and rules.rsi_entry_min <= rsi <= rules.rsi_entry_max
    can_trade = (
        direction == "看多（强）"
        and risk_complete
        and not heat_block
        and not vol_high
        and not high_positions
        and entry_rsi
        and confidence != "低"
    )
    conditions = [
        row
        for row in reassessment
        if row["key"]
        in ("heat_exit", "vol_exit", "position_exit", "strong_bull", "fresh_data")
    ]
    if direction == "无法判断":
        trading_label, trading_reason = (
            "无法给短期交易参考",
            "本市场必需方向数据缺失或过期",
        )
    elif can_trade:
        trading_label = (
            "可考虑技术性反弹交易"
            if long_term["direction"] == "看空"
            else "可考虑顺势做多"
            if long_term["direction"] == "看多"
            else "可考虑技术性顺势交易"
        )
        trading_reason = (
            "强看多且风险门槛通过；与长期看空判断相反"
            if long_term["direction"] == "看空"
            else "强看多且风险门槛通过，停止新增条件需持续复核"
        )
    elif direction == "看空（强）":
        trading_label, trading_reason = (
            "不宜做多",
            "下跌趋势尚未扭转；超卖不构成买入理由",
        )
    elif direction != "看多（强）":
        trading_label, trading_reason = (
            "观望为主，不建议主动开仓",
            "偏多等弱方向不足以支持主动开仓，须等待完整强看多规则",
        )
    else:
        trading_label, trading_reason = (
            "不主动追入，等待风险解除",
            "风险数据不足、追高/高波动/历史高位限制或RSI买入范围尚未通过",
        )
    long_direction = long_term["direction"]
    batch = None
    if long_direction == "无法判断":
        label, reason = "无法给长期配置建议", "长期核心数据缺失或过期"
    elif long_direction == "看空":
        label, reason = "暂缓长期配置", "短期反弹未改变长期看空判断"
    elif long_direction == "中性":
        label, reason = "等待长期方向明确", "长期配置理由尚不充分"
    elif long_term["confidence"] == "低":
        label, reason = (
            "先补齐需求证据再考虑主动新增",
            "保留长期看多方向，但长期证据支撑度低",
        )
    elif can_trade:
        label, reason = (
            "可考虑分批配置",
            "长期看多、短期强看多且风险门槛通过；需求验证不完整时仍须持续核验",
        )
        batch = "原则示例：计划买入可分3—4次；相对上次自行买入参考价格回调约1个最新ATR且重估条件仍满足，再考虑下一次；不输出具体买入价或仓位，条件恶化即暂停。"
    elif direction == "看多（强）":
        label, reason = "当前不追涨", "需先解除风险限制、补齐关键数据并复核RSI买入范围"
    else:
        label, reason = "当前暂不新增", "等待短期完整强看多与风险条件改善"
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
            "status": "有效" if valid else "样本不足",
        }
    return {
        "name": series.name
        if series
        else ("国内黄金" if key == "domestic" else "国际黄金"),
        "symbol": "Au99.99" if key == "domestic" else "XAU",
        "unit": series.unit
        if series
        else ("元/克" if key == "domestic" else "美元/金衡盎司"),
        "status": series.status
        if series and fresh
        else "unavailable"
        if not bars
        else "stale",
        "source": series.source if series else None,
        "source_url": series.source_url if series else None,
        "as_of": bars[-1].trade_date.isoformat() if bars else None,
        "latest_close": values[-1] if values else None,
        "daily_change_pct": (values[-1] / values[-2] - 1) * 100
        if len(values) > 1
        else None,
        "performance": performance,
        "short_term": {
            "direction": direction,
            "reason": "按本市场均线排列和20日MA60斜率分档，风险不改变方向"
            if direction != "无法判断"
            else "本市场至少80个有效收盘点或新鲜度不足",
            "confidence": confidence,
            "confidence_reasons": reasons,
        },
        "indicators": indicators,
        "price_position": position,
        "risks": risks,
        "reassessment": reassessment,
        "allocation": {
            "label": label,
            "reason": reason,
            "conditions": conditions,
            "batch_guidance": batch,
        },
        "trading": {
            "label": trading_label,
            "reason": trading_reason,
            "scope": "仅为技术面参考，不构成长期配置依据",
            "conditions": conditions,
        },
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
        or any(s.status == "unavailable" for s in (domestic, international, fx))
        or any(_future_publication(s, collection.generated_at) for s in (domestic, international, fx))
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
    """以当前事实生成可归档的JSON兼容研究结果，不调用网络或AI。"""
    rules = rules or GoldRules()
    long_term = _long_term(collection, rules)
    return {
        "long_term": long_term,
        "markets": {
            key: _market(key, collection.series.get(key), collection, long_term, rules)
            for key in ("domestic", "international")
        },
        "fx_decomposition": _fx_decomposition(collection, rules),
    }

"""黄金研判规则回归；合成序列只用于规则边界，不代表市场业绩。"""

from datetime import UTC, date, datetime, timedelta

import pytest

from gold.analysis import (
    _change_rank,
    _short_direction,
    _wilder_atr,
    _wilder_rsi,
    analyze_collection,
)
from gold.models import (
    GoldBar,
    GoldCollection,
    GoldObservation,
    GoldRules,
    GoldSeries,
)

NOW = datetime(2026, 10, 26, tzinfo=UTC)
END = NOW.date()


def series(key, values, *, end=END, unit=None, monthly=False, bars=False):
    units = {
        "real_yield": "%",
        "dollar": "指数",
        "holdings": "吨",
        "fx": "人民币/美元",
        "domestic": "元/克",
        "international": "美元/金衡盎司",
    }
    days = [end - timedelta(days=len(values) - i - 1) for i in range(len(values))]
    if monthly:
        days = []
        # 当前未结束的统计月不能提前生成月末观察值。
        completed_month = end.year * 12 + end.month
        if (end + timedelta(days=1)).month == end.month:
            completed_month -= 1
        for i in range(len(values)):
            month = completed_month - len(values) + i
            year, m = divmod(month, 12)
            next_month = date(year + (m == 11), (m + 1) % 12 + 1, 1)
            days.append(next_month - timedelta(days=1))
    return GoldSeries(
        key=key,
        name=key,
        source="测试",
        source_url="https://example.com",
        unit=unit or units.get(key, "万盎司"),
        fetched_at=NOW,
        frequency="monthly" if monthly else "daily",
        observations=[]
        if bars
        else [
            GoldObservation(observation_date=d, value=v)
            for d, v in zip(days, values, strict=True)
        ],
        bars=[
            GoldBar(trade_date=d, open=v, high=v + 1, low=v - 1, close=v)
            for d, v in zip(days, values, strict=True)
        ]
        if bars
        else [],
    )


def collection(**items):
    return GoldCollection(generated_at=NOW, series=items)


def test_new_report_exposes_observations_without_composite_or_actions():
    data = cores()
    data["china_reserves"] = series(
        "china_reserves", list(range(100, 109)), monthly=True
    )
    data["domestic"] = series(
        "domestic", [100 + i * 0.1 for i in range(100)], bars=True
    )
    result = analyze_collection(collection(**data))
    assert set(result) == {"observations", "markets", "fx_decomposition"}
    macro = {item["key"]: item for item in result["observations"]}
    assert macro["real_yield"]["main"]["change"] == pytest.approx(-120)
    assert macro["real_yield"]["latest_value"] == pytest.approx(1.51)
    assert macro["china_reserves"]["main"]["change"] == 6
    assert macro["global_reserves"]["state"] == "数据缺失"
    for item in macro.values():
        assert not {"weight", "role", "score", "signal", "significance"} & item.keys()
        for window in ("main", "marginal"):
            assert (
                not {"score", "signal", "significance", "threshold"}
                & item[window].keys()
            )
    market = result["markets"]["domestic"]
    assert not {"allocation", "trading", "reassessment"} & market.keys()
    assert market["daily_change"] == pytest.approx(0.1)
    assert market["symbol"] == "Au99.99"
    assert market["short_term"]["direction"] == "看多（强）"
    assert "20日均线" in market["short_term"]["reason"]
    assert not any(r["key"] == "overheated" for r in market["risks"])
    assert not any(
        word in str(result) for word in ("开仓", "买入", "分批", "持仓比例", "止损")
    )


def test_short_state_and_risk_are_unknown_when_market_is_stale():
    stale = series(
        "domestic",
        [100 + i * 0.1 for i in range(100)],
        bars=True,
        end=END - timedelta(days=11),
    )
    market = analyze_collection(collection(domestic=stale))["markets"]["domestic"]
    assert market["short_term"]["direction"] == "无法判断"
    assert market["status"] == "stale"
    assert (
        next(row for row in market["risks"] if row["key"] == "oversold")["active"]
        is None
    )


def cores(bull=True):
    return {
        "real_yield": series(
            "real_yield", [3 - i * 0.01 if bull else 1 + i * 0.01 for i in range(150)]
        ),
        "dollar": series(
            "dollar", [120 - i * 0.1 if bull else 100 + i * 0.1 for i in range(150)]
        ),
    }


@pytest.mark.parametrize(
    ("close", "fast", "slow", "slope", "expected"),
    [
        (102, 101, 100, 0.50001, "看多（强）"),
        (98, 99, 100, -0.50001, "看空（强）"),
        (102, 101, 100, 0.5, "偏多"),
        (98, 99, 100, -0.5, "偏空"),
        (100, 100, 100, 0, "中性"),
        (102, 101, 100, -0.50001, "中性"),
    ],
)
def test_five_short_directions_and_exact_flat_boundaries(
    close, fast, slow, slope, expected
):
    assert _short_direction(close, fast, slow, slope, GoldRules()) == expected


def test_wilder_rsi_and_atr_use_recursive_smoothing():
    values = [100.0 + i for i in range(15)] + [113.0, 114.0]
    assert _wilder_rsi(values, 14) == pytest.approx(
        100 - 100 / (1 + (13 / 14 * 13 / 14 + 1 / 14) / (1 / 14 * 13 / 14))
    )
    bars = series("domestic", values, bars=True).bars
    assert _wilder_atr(bars, 14) == pytest.approx(2)
    assert _wilder_rsi([100] * 20, 14) == 50


def test_change_rank_midrank_requires_five_year_history_and_ignores_future():
    points = series("holdings", [100] * 2200).observations
    result = _change_rank(points, 120, "holdings", GoldRules())
    assert result["percentile"] == 50
    short = _change_rank(points[-1000:], 120, "holdings", GoldRules())
    assert short["percentile"] is None
    future = GoldObservation(observation_date=END + timedelta(days=1), value=999)
    s = series("real_yield", [3 - i * 0.01 for i in range(150)])
    s.observations.append(future)
    result = analyze_collection(collection(real_yield=s, dollar=cores()["dollar"]))
    assert result["observations"][0]["as_of"] == END.isoformat()


def test_monthly_90_day_age_and_missing_month_not_stitched():
    items = cores()
    reserve = series(
        "china_reserves",
        list(range(100, 109)),
        end=END - timedelta(days=120),
        monthly=True,
    )
    items["china_reserves"] = reserve
    result = analyze_collection(collection(**items))
    assert (
        next(f for f in result["observations"] if f["key"] == "china_reserves")["state"]
        == "数据缺失"
    )
    reserve = series("china_reserves", list(range(100, 109)), monthly=True)
    reserve.observations.pop(-3)
    items["china_reserves"] = reserve
    factor = next(
        f
        for f in analyze_collection(collection(**items))["observations"]
        if f["key"] == "china_reserves"
    )
    assert factor["main"]["change"] is None
    assert factor["state"] == "数据不足"


def test_fx_symmetric_interaction_and_common_dates():
    domestic = series("domestic", [100 + i for i in range(70)], bars=True)
    international = series("international", [100 + i * 2 for i in range(70)], bars=True)
    fx = series("fx", [6 + i * 0.01 for i in range(70)])
    fx.observations.pop(-2)
    result = analyze_collection(
        collection(domestic=domestic, international=international, fx=fx)
    )["fx_decomposition"]
    assert [r["window"] for r in result] == [20, 60]
    for row in result:
        assert row["international_contribution_pp"] + row["fx_contribution_pp"] + row[
            "residual_pp"
        ] == pytest.approx(row["domestic_return_pct"])
        assert row["interaction_pp"] == pytest.approx(
            row["international_return_pct"] * row["fx_return_pct"] / 100
        )
    fx.unit = "美元/人民币"
    assert (
        analyze_collection(
            collection(domestic=domestic, international=international, fx=fx)
        )["fx_decomposition"]
        == []
    )


def tradeable_price():
    """旧历史高且波动大，近期低位温和回升；不使用未来数据。"""
    import math

    values = [200 + 20 * math.sin(i) for i in range(2150)]
    values += [100 + i * 0.1 + 0.5 * math.sin(i * 2) for i in range(150)]
    return series("domestic", values, bars=True)


def test_high_position_limits_buying_without_reversing_direction():
    values = [100 + i * 0.1 for i in range(2300)]
    market = analyze_collection(
        collection(domestic=series("domestic", values, bars=True))
    )["markets"]["domestic"]
    assert market["short_term"]["direction"] == "看多（强）"
    assert market["price_position"]["3y"]["percentile"] > 99
    assert market["price_position"]["5y"]["percentile"] > 99
    assert next(r for r in market["risks"] if r["key"] == "historical_high")["active"]
    assert "赔率" not in str(market)


def test_exact_monthly_freshness_boundary():
    reserve = series("china_reserves", list(range(100, 109)), monthly=True)
    last = reserve.observations[-1].observation_date
    base = collection(china_reserves=reserve, **cores())
    base.generated_at = datetime.combine(
        last + timedelta(days=90), datetime.min.time(), tzinfo=UTC
    )
    factor = next(
        f
        for f in analyze_collection(base)["observations"]
        if f["key"] == "china_reserves"
    )
    assert factor["state"] == "有效"
    base.generated_at += timedelta(days=1)
    factor = next(
        f
        for f in analyze_collection(base)["observations"]
        if f["key"] == "china_reserves"
    )
    assert factor["state"] == "数据缺失"


def test_conflicting_duplicate_is_isolated_and_flat_atr_cannot_create_signal():
    domestic = series("domestic", [100] * 100, bars=True)
    duplicate = domestic.bars[-1].model_copy(update={"close": 100.5})
    domestic.bars.append(duplicate)
    result = analyze_collection(
        collection(domestic=domestic, international=tradeable_price())
    )
    assert result["markets"]["domestic"]["short_term"]["direction"] == "无法判断"
    assert result["markets"]["international"]["short_term"]["direction"] == "看多（强）"
    flat = series("domestic", [100] * 100, bars=True)
    flat.bars = [
        b.model_copy(update={"high": b.close, "low": b.close}) for b in flat.bars
    ]
    market = analyze_collection(collection(domestic=flat))["markets"]["domestic"]
    assert market["indicators"]["atr14"] == 0
    assert market["indicators"]["deviation_atr"] is None
    assert market["short_term"]["direction"] == "中性"


@pytest.mark.parametrize(("gold_step", "fx_step"), [(0, 0), (-0.2, 0.01), (0.2, -0.01)])
def test_fx_zero_and_negative_components_are_finite_json(gold_step, fx_step):
    import json

    result = analyze_collection(
        collection(
            domestic=series("domestic", [100 + i * 0.1 for i in range(70)], bars=True),
            international=series(
                "international", [100 + i * gold_step for i in range(70)], bars=True
            ),
            fx=series("fx", [7 + i * fx_step for i in range(70)]),
        )
    )
    assert len(result["fx_decomposition"]) == 2
    json.dumps(result, allow_nan=False)
    for row in result["fx_decomposition"]:
        assert row["international_contribution_pp"] + row["fx_contribution_pp"] + row[
            "residual_pp"
        ] == pytest.approx(row["domestic_return_pct"])


def test_publication_freshness_and_distinct_reserve_samples():
    items = cores()
    items["china_reserves"] = series(
        "china_reserves", list(range(100, 109)), monthly=True
    )
    items["global_reserves"] = series(
        "global_reserves", list(range(200, 209)), monthly=True
    )
    items["real_yield"].published_at = NOW + timedelta(hours=6)
    observations = {
        row["key"]: row
        for row in analyze_collection(collection(**items))["observations"]
    }
    assert observations["real_yield"]["state"] == "数据缺失"
    assert observations["china_reserves"]["main"]["change"] == 6
    assert observations["global_reserves"]["main"]["change"] == 6
    items["real_yield"].metadata["published_precision"] = "date"
    assert analyze_collection(collection(**items))["observations"][0]["state"] == "有效"


def test_observation_age_boundaries_and_stale_source_error():
    items = cores()
    for key, limit in (("real_yield", 10), ("dollar", 14)):
        original = items[key]
        for age, state in ((limit, "有效"), (limit + 1, "数据缺失")):
            items[key] = series(
                key,
                [p.value for p in original.observations],
                end=END - timedelta(days=age),
            )
            rows = analyze_collection(collection(**items))["observations"]
            assert next(row for row in rows if row["key"] == key)["state"] == state
        items[key] = original
    items["dollar"].status = "stale"
    items["dollar"].error = "刷新超时"
    row = analyze_collection(collection(**items))["observations"][1]
    assert row["source_status"] == "stale" and row["error"] == "刷新超时"


def test_short_reason_describes_observed_position_not_assumed_trend():
    market = analyze_collection(
        collection(domestic=series("domestic", [100] * 100, bars=True))
    )["markets"]["domestic"]
    assert market["short_term"]["direction"] == "中性"
    assert "等于20日均线" in market["short_term"]["reason"]
    assert "等于60日均线" in market["short_term"]["reason"]
    assert "持平" in market["short_term"]["reason"]


def test_missing_reference_does_not_remove_available_macro_facts():
    observations = {
        r["key"]: r for r in analyze_collection(collection(**cores()))["observations"]
    }
    assert observations["real_yield"]["main"]["change"] == pytest.approx(-120)
    assert observations["global_reserves"]["state"] == "数据缺失"
    assert (
        observations["real_yield"]["main"]["percentile_status"]
        == "五年变化分布及预热样本不足"
    )


def test_fx_decomposition_rejects_outdated_last_common_day():
    domestic = series("domestic", [100 + i for i in range(70)], bars=True)
    international = series("international", [100 + i for i in range(70)], bars=True)
    fx = series("fx", [7 + i * 0.01 for i in range(70)])
    # 各来源最新值仍新鲜，但三者最后共同日期已超过有效期。
    domestic.bars = domestic.bars[:-5] + [domestic.bars[-1]]
    international.bars = international.bars[:-5] + [international.bars[-2]]
    fx.observations = fx.observations[:-5] + [fx.observations[-3]]
    assert analyze_collection(collection(domestic=domestic, international=international, fx=fx), GoldRules(daily_max_age_days=3))["fx_decomposition"] == []

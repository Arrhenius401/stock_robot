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


def test_same_day_precise_future_publication_cannot_form_long_signal():
    items = cores()
    items['real_yield'].published_at = NOW + timedelta(hours=6)
    result = analyze_collection(collection(**items))
    assert result['long_term']['direction'] == '无法判断'
    items['real_yield'].metadata['published_precision'] = 'date'
    assert analyze_collection(collection(**items))['long_term']['direction'] != '无法判断'


def cores(bull=True):
    return {
        "real_yield": series(
            "real_yield", [3 - i * 0.01 if bull else 1 + i * 0.01 for i in range(150)]
        ),
        "dollar": series(
            "dollar", [120 - i * 0.1 if bull else 100 + i * 0.1 for i in range(150)]
        ),
    }


def test_core_direction_survives_reference_missing_with_low_support():
    result = analyze_collection(collection(**cores()))["long_term"]
    assert result["direction"] == "看多"
    assert result["confidence"] == "低"
    assert result["windows"]["main"]["core_score"] == 4
    assert result["windows"]["main"]["reference_score"] == 0
    assert result["missing"]


def test_reference_cannot_reverse_conflicting_cores_and_reserves_one_vote():
    items = cores()
    items["dollar"] = cores(False)["dollar"]
    items["holdings"] = series("holdings", [100 + i for i in range(150)])
    items["global_reserves"] = series(
        "global_reserves", list(range(100, 109)), monthly=True
    )
    items["china_reserves"] = series(
        "china_reserves", list(range(100, 109)), monthly=True
    )
    result = analyze_collection(collection(**items))["long_term"]
    assert result["direction"] == "中性"
    assert result["windows"]["main"]["reference_score"] == 2
    assert (
        next(f for f in result["factors"] if f["key"] == "china_reserves")["weight"]
        == 0
    )


def test_main_marginal_opposition_and_missing_core_are_distinct():
    items = cores()
    values = [3 - i * 0.025 for i in range(90)] + [0.8 + i * 0.015 for i in range(60)]
    items["real_yield"] = series("real_yield", values)
    items["dollar"] = series(
        "dollar",
        [150 - i * 0.5 for i in range(90)] + [105 + i * 0.1 for i in range(60)],
    )
    result = analyze_collection(collection(**items))["long_term"]
    assert result["windows"]["main"]["direction"] == "看多"
    assert result["windows"]["marginal"]["direction"] == "看空"
    assert result["direction"] == "中性"
    items.pop("dollar")
    assert (
        analyze_collection(collection(**items))["long_term"]["direction"] == "无法判断"
    )


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
    assert result["long_term"]["direction"] == "看多"
    assert result["long_term"]["factors"][0]["as_of"] == END.isoformat()


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
        next(f for f in result["long_term"]["factors"] if f["key"] == "china_reserves")[
            "state"
        ]
        == "数据缺失"
    )
    reserve = series("china_reserves", list(range(100, 109)), monthly=True)
    reserve.observations.pop(-3)
    items["china_reserves"] = reserve
    factor = next(
        f
        for f in analyze_collection(collection(**items))["long_term"]["factors"]
        if f["key"] == "china_reserves"
    )
    assert factor["main"]["signal"] == "数据缺失"


def test_price_direction_survives_macro_and_risk_missing_but_no_active_trade():
    market = analyze_collection(
        collection(
            domestic=series("domestic", [100 + i for i in range(100)], bars=True)
        )
    )["markets"]["domestic"]
    assert market["short_term"]["direction"] == "看多（强）"
    assert market["short_term"]["confidence"] == "低"
    assert market["trading"]["label"] != "可考虑顺势做多"
    assert market["allocation"]["label"] == "无法给长期配置建议"
    assert market["price_position"]["3y"]["percentile"] is None
    assert any(r["key"] == "overheated" and r["active"] for r in market["risks"])
    assert "赔率" not in str(market)


def test_expired_market_and_reference_do_not_contaminate_other_directions():
    items = cores()
    items["domestic"] = series(
        "domestic",
        [100 + i for i in range(100)],
        end=END - timedelta(days=11),
        bars=True,
    )
    items["international"] = series(
        "international", [100 + i for i in range(100)], bars=True
    )
    result = analyze_collection(collection(**items))
    assert result["long_term"]["direction"] == "看多"
    assert result["markets"]["domestic"]["short_term"]["direction"] == "无法判断"
    assert result["markets"]["international"]["short_term"]["direction"] == "看多（强）"


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


def test_short_trade_remains_available_with_bearish_or_missing_macro():
    market_series = tradeable_price()
    result = analyze_collection(collection(domestic=market_series, **cores(False)))
    market = result["markets"]["domestic"]
    assert market["short_term"]["direction"] == "看多（强）"
    assert market["short_term"]["confidence"] == "高"
    assert market["allocation"]["label"] == "暂缓长期配置"
    assert market["trading"]["label"] == "可考虑技术性反弹交易"
    assert market["allocation"]["batch_guidance"] is None
    assert "相反" in market["trading"]["reason"]
    market = analyze_collection(collection(domestic=market_series))["markets"][
        "domestic"
    ]
    assert market["allocation"]["label"] == "无法给长期配置建议"
    assert market["trading"]["label"] == "可考虑技术性顺势交易"


def test_low_long_support_blocks_allocation_but_not_technical_trade():
    market = analyze_collection(collection(domestic=tradeable_price(), **cores()))[
        "markets"
    ]["domestic"]
    assert market["allocation"]["label"] == "先补齐需求证据再考虑主动新增"
    assert market["trading"]["label"] == "可考虑顺势做多"
    assert market["allocation"]["batch_guidance"] is None


def test_conditional_batch_only_with_long_and_short_bull_and_valid_risks():
    items = cores()
    items["domestic"] = tradeable_price()
    items["holdings"] = series("holdings", [100 + i * 0.1 for i in range(150)])
    result = analyze_collection(collection(**items))
    assert result["long_term"]["confidence"] == "中"
    market = result["markets"]["domestic"]
    assert market["allocation"]["label"] == "可考虑分批配置"
    assert "3—4次" in market["allocation"]["batch_guidance"]
    assert "最新ATR" in market["allocation"]["batch_guidance"]
    assert "暂停" in market["allocation"]["batch_guidance"]


def test_reassessment_has_quantified_entry_exit_bands_and_unknown_state_is_conservative(
    monkeypatch,
):
    from gold import analysis

    s = tradeable_price()
    monkeypatch.setattr(analysis, "_wilder_rsi", lambda *_: 68.0)
    market = analyze_collection(collection(domestic=s))["markets"]["domestic"]
    hot = next(r for r in market["risks"] if r["key"] == "overheated")
    assert hot["active"] is True
    assert hot["threshold"] == {"rsi": 70, "atr": 2}
    exit_rule = next(r for r in market["reassessment"] if r["key"] == "heat_exit")
    assert exit_rule["threshold"] == {"rsi_lte": 65, "atr_lte": 1}
    assert exit_rule["met"] is False
    monkeypatch.setattr(analysis, "_wilder_rsi", lambda *_: 65.0)
    market = analyze_collection(collection(domestic=s))["markets"]["domestic"]
    assert (
        next(r for r in market["reassessment"] if r["key"] == "heat_exit")["met"]
        is True
    )
    assert market["trading"]["label"] == "可考虑技术性顺势交易"


def test_high_position_limits_buying_without_reversing_direction():
    values = [100 + i * 0.1 for i in range(2300)]
    market = analyze_collection(
        collection(domestic=series("domestic", values, bars=True))
    )["markets"]["domestic"]
    assert market["short_term"]["direction"] == "看多（强）"
    assert market["price_position"]["3y"]["percentile"] > 99
    assert market["price_position"]["5y"]["percentile"] > 99
    assert next(r for r in market["risks"] if r["key"] == "historical_high")["active"]
    assert market["trading"]["label"] == "不主动追入，等待风险解除"
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
        for f in analyze_collection(base)["long_term"]["factors"]
        if f["key"] == "china_reserves"
    )
    assert factor["state"] == "有效"
    base.generated_at += timedelta(days=1)
    factor = next(
        f
        for f in analyze_collection(base)["long_term"]["factors"]
        if f["key"] == "china_reserves"
    )
    assert factor["state"] == "数据缺失"


def test_marginal_threshold_configuration_does_not_change_main_window():
    items = cores()
    rules = GoldRules(rate_marginal_threshold_bp=999, dollar_marginal_threshold_pct=999)
    result = analyze_collection(collection(**items), rules)["long_term"]
    assert result["windows"]["main"]["direction"] == "看多"
    assert result["windows"]["marginal"]["direction"] == "中性"
    assert result["direction"] == "中性"


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
    assert market["trading"]["label"] == "观望为主，不建议主动开仓"


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


def test_complete_global_evidence_high_support_and_stale_cache_downgrades():
    items = {
        "real_yield": series("real_yield", [30 - i * 0.01 for i in range(2200)]),
        "dollar": series("dollar", [400 - i * 0.1 for i in range(2200)]),
        "holdings": series("holdings", [100 + i * 0.1 for i in range(2200)]),
        "global_reserves": series(
            "global_reserves", list(range(100, 109)), monthly=True
        ),
    }
    result = analyze_collection(collection(**items))["long_term"]
    assert result["confidence"] == "高"
    assert result["direction"] == "看多"
    items["dollar"].status = "stale"
    items["dollar"].error = "刷新超时"
    result = analyze_collection(collection(**items))["long_term"]
    assert result["confidence"] == "中"
    assert result["direction"] == "看多"
    factor = next(f for f in result["factors"] if f["key"] == "dollar")
    assert factor["source_status"] == "stale"
    assert factor["error"] == "刷新超时"
    assert factor["as_of"] == END.isoformat()


def test_daily_and_h10_age_gates_use_observation_not_fetch_time():
    items = cores()
    items["real_yield"] = series(
        "real_yield", [3 - i * 0.01 for i in range(150)], end=END - timedelta(days=10)
    )
    items["dollar"] = series(
        "dollar", [120 - i * 0.1 for i in range(150)], end=END - timedelta(days=14)
    )
    result = analyze_collection(collection(**items))["long_term"]
    assert result["direction"] == "看多"
    items["real_yield"] = series(
        "real_yield", [3 - i * 0.01 for i in range(150)], end=END - timedelta(days=11)
    )
    assert (
        analyze_collection(collection(**items))["long_term"]["direction"] == "无法判断"
    )
    items["real_yield"] = cores()["real_yield"]
    items["dollar"] = series(
        "dollar", [120 - i * 0.1 for i in range(150)], end=END - timedelta(days=15)
    )
    assert (
        analyze_collection(collection(**items))["long_term"]["direction"] == "无法判断"
    )


def test_fixed_threshold_and_significance_are_separate_and_direction_oriented():
    items = {
        "real_yield": series("real_yield", [30 - i * 0.01 for i in range(2200)]),
        "dollar": series("dollar", [400 - i * 0.1 for i in range(2200)]),
    }
    items["real_yield"].observations[-1].value -= 1
    result = analyze_collection(collection(**items))["long_term"]
    factor = next(f for f in result["factors"] if f["key"] == "real_yield")
    assert factor["main"]["signal"] == "利多"
    assert factor["main"]["significance"] == "显著利多"
    assert factor["main"]["percentile"] == 100
    assert factor["main"]["rank_end"] < factor["main"]["end"]


def test_negative_real_yield_is_valid_and_global_falls_back_to_china_once():
    items = cores()
    items["real_yield"] = series("real_yield", [-1 - i * 0.01 for i in range(150)])
    items["china_reserves"] = series(
        "china_reserves", list(range(100, 109)), monthly=True
    )
    result = analyze_collection(collection(**items))["long_term"]
    assert result["direction"] == "看多"
    assert result["windows"]["main"]["reference_score"] == 1
    assert result["confidence"] == "中"
    china = next(f for f in result["factors"] if f["key"] == "china_reserves")
    assert china["weight"] == 1
    assert any("仅中国样本" in n for n in china["notes"])

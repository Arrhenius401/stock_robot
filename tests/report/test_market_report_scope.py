"""三市场共享报告范围，市场采集、估值与港股特殊展示保持独立。"""
from datetime import date, timedelta
from typing import Literal

import pytest

from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    DimensionSufficiency,
    PriceData,
    SufficiencyLevel,
    ValuationData,
)
from report.payload import build_stock_payload
from report.quality import build_data_quality
from report.scoring import build_report, compute_price_info
from report.signal import SignalConfig

MARKETS = [("a-shares", "600519"), ("hk", "00700.HK"), ("us", "AAPL")]


def context(market, symbol, count):
    ctx = AnalysisContext(symbol=symbol, name="已确认名称", market=market, identity_status="confirmed")
    ctx.price_data = [PriceData(symbol=symbol, trade_date=date(2026, 1, 1) + timedelta(days=i),
                               open=10, high=12, low=8, close=11, volume=100) for i in range(count)]
    for key in ("financial", "price", "valuation", "industry"):
        setattr(ctx.sufficiency, key, DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=60))
    return ctx


@pytest.mark.parametrize("market,symbol", MARKETS)
@pytest.mark.parametrize("count,scored,expected", [(0, 4, "facts_only"), (1, 2, "partial"),
                                                   (19, 1, "facts_only"), (60, 4, "complete")])
def test_scope_and_partial_label_across_markets(market, symbol, count, scored, expected):
    ctx = context(market, symbol, count)
    keys: list[Literal["financial", "technical", "valuation", "industry"]] = ["financial", "valuation", "technical", "industry"]
    results = [AnalysisResult(dimension=key, status="ok", score=7, summary="已核实") for key in keys[:scored]]
    payload = build_stock_payload(symbol, ctx.name, results, {}, ctx, no_llm=True, signal_cfg=SignalConfig())
    assert payload["assessment_status"] == expected
    assert payload["report_scope"] == {"complete": "full", "partial": "limited", "facts_only": "facts_only"}[expected]
    markdown = build_report(symbol, ctx.name, results, {}, ctx, no_llm=True)
    if expected == "partial":
        assert "部分评估综合得分（参考）" in markdown
        assert payload["score"]["final"] == 7
    elif expected == "facts_only":
        assert payload["score"]["final"] is None
    if expected != "complete":
        assert payload["signal"] is None
    assert "近一年最高价" not in markdown and "近一年最低价" not in markdown
    assert "HSICS" in markdown if market == "hk" else True


@pytest.mark.parametrize("market,symbol", MARKETS)
def test_required_signal_dimensions_remain_market_specific(market, symbol):
    ctx = context(market, symbol, 60)
    results = [AnalysisResult(dimension=key, status="ok", score=7, summary="已核实")
               for key in ("financial", "technical", "valuation")]
    quality = build_data_quality(ctx, results)
    assert quality["signal_allowed"] == (market != "us")
    assert quality["required_dimensions"] == ["financial", "technical", "valuation", *(["industry"] if market == "us" else [])]
    ctx.sufficiency.price = DimensionSufficiency(level=SufficiencyLevel.PARTIAL)
    assert not build_data_quality(ctx, results)["signal_allowed"]


@pytest.mark.parametrize("market,symbol", MARKETS)
@pytest.mark.parametrize("count", [0, 2, 19, 20, 60])
def test_price_labels_describe_collected_window(market, symbol, count):
    ctx = context(market, symbol, count)
    info = compute_price_info(ctx)
    assert info["price_bars"] == count
    if count < 20:
        assert "不计算" in info["price_position"]
    report = build_report(symbol, ctx.name, [], {}, ctx, no_llm=True)
    assert "近一年最高价" not in report and "近一年最低价" not in report


@pytest.mark.parametrize("market,symbol", MARKETS)
@pytest.mark.parametrize("count", [0, 60])
def test_measured_valuation_does_not_require_daily_bars(market, symbol, count):
    from data.enrichers.price_enricher import PriceEnricher
    from data.enrichers.valuation_enricher import ValuationEnricher

    ctx = context(market, symbol, count)
    ctx.valuation_data = ValuationData(symbol=symbol, date=date(2026, 11, 20), pe_ttm=20, pb=2)
    PriceEnricher().enrich(ctx)
    ValuationEnricher().enrich(ctx)
    if market == "a-shares":
        assert ctx.enriched_valuation is None
        assert ctx.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT
    else:
        assert ctx.enriched_valuation is not None
        assert ctx.sufficiency.valuation.level != SufficiencyLevel.INSUFFICIENT

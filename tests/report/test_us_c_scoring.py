"""美股四核心信号门槛与五维报告回归。"""
from datetime import date, timedelta
from typing import Literal

import pytest

from analysis.config_loader import ConfigLoader
from analysis.sentiment import SentimentAnalyzer
from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    DimensionSufficiency,
    IndustryData,
    PriceData,
    SufficiencyLevel,
)
from report.payload import build_stock_payload
from report.quality import build_data_quality
from report.scoring import build_report, compute_score_summary


def context(market="us"):
    ctx = AnalysisContext(symbol="AAPL", name="苹果公司", market=market, extra_analysis_enabled=True)
    ctx.industry_data = IndustryData(symbol="AAPL", industry="系统软件", sector="")
    ctx.price_data = [PriceData(symbol="AAPL", trade_date=date(2026, 1, 1) + timedelta(days=i),
                                open=10, high=12, low=8, close=11, volume=100) for i in range(60)]
    for key in ("financial", "price", "valuation", "industry"):
        setattr(ctx.sufficiency, key, DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT))
    return ctx


def results():
    values: list[tuple[Literal["financial", "technical", "valuation", "industry"], float]] = [
        ("financial", 8), ("technical", 6), ("valuation", 7), ("industry", 5)]
    return [AnalysisResult(dimension=key, status="ok", score=score, summary="测试") for key, score in values]


@pytest.mark.parametrize("dimension", ["financial", "technical", "valuation", "industry"])
@pytest.mark.parametrize("missing", [True, False])
def test_us_needs_every_core_score_and_ok_status(dimension, missing):
    values = results()
    item = next(r for r in values if r.dimension == dimension)
    if missing:
        item.score = None
    else:
        item.status = "partial"
    assert not build_data_quality(context(), values)["signal_allowed"]


def test_industry_sufficiency_is_required_only_for_us():
    for market in ("us", "a-shares", "hk"):
        ctx = context(market)
        ctx.sufficiency.industry = DimensionSufficiency(level=SufficiencyLevel.PARTIAL)
        quality = build_data_quality(ctx, results())
        assert quality["signal_allowed"] == (market != "us")
        if market == "us":
            assert "行业" in quality["signal_unavailable_reason"]


def test_us_zero_industry_score_is_valid_and_news_unscored():
    ctx = context()
    values = results()
    values[-1].score = 0
    sentiment = SentimentAnalyzer().analyze(ctx)
    assert sentiment.score is None
    assert "舆情本期未计分" in sentiment.score_detail
    values.append(sentiment)
    quality = build_data_quality(ctx, values)
    assert quality["signal_allowed"]
    assert quality["has_scored_data"]
    assert quality["applicable_dimensions"] == 5
    assert "美股舆情本期未计分" in quality["coverage_basis"]
    summary = compute_score_summary(values)
    assert summary.final_score == round((8*30+6*20+7*25)/95, 1)
    assert summary.score_rows[-1]["score"] == "N/A"
    payload = build_stock_payload(ctx.symbol, ctx.name, values, {}, ctx, no_llm=True)
    assert len(payload["score_rows"]) == 5
    assert payload["overview"]["industry"] == "系统软件"
    report = build_report(ctx.symbol, ctx.name, values, {}, ctx, no_llm=True)
    assert "不是 GICS/申万" in report
    assert "未做美股历史回测校准" in report
    assert "财报金额币种和会计准则以来源证据为准" in report
    assert "本期美股不提供综合评分" not in report


def test_complete_us_core_generates_only_rule_label_and_partial_closes_it():
    from report.signal import SignalConfig

    ctx = context()
    values = results()
    payload = build_stock_payload(ctx.symbol, ctx.name, values, {}, ctx, no_llm=True, signal_cfg=SignalConfig())
    assert payload['signal'] is not None
    assert set(payload['signal']) == {'level', 'label', 'basis'}
    assert '胜率' not in payload['signal']['basis']
    values[0].status = 'partial'
    payload = build_stock_payload(ctx.symbol, ctx.name, values, {}, ctx, no_llm=True, signal_cfg=SignalConfig())
    assert payload['signal'] is None


@pytest.mark.parametrize(("industry", "strategy"), [("综合性银行", "BankScorer"),
    ("财产与意外伤害保险", "NonBankFinancialScorer"), ("系统软件", "TechGrowthScorer")])
def test_us_template_uses_real_industry_and_preserves_mapping(industry, strategy):
    ctx = context()
    assert ctx.industry_data is not None
    ctx.industry_data.industry = industry
    config = ConfigLoader().load_for_context(ctx)
    assert config["meta"]["cache_key"].startswith("us::")
    assert config["meta"]["strategy_key"] == strategy
    assert not config["meta"]["scoring_template_fallback"]
    assert ctx.industry_data.industry == industry


def test_industry_cutoff_keeps_cached_snapshot_datetime():
    from data.schemas import EnrichedIndustry

    ctx = context()
    ctx.enriched_industry = EnrichedIndustry(peer_count=5, comparison={
        "valuation": {"as_of": "2026-11-15T12:00:00+08:00"}})
    quality = build_data_quality(ctx, results())
    industry = next(row for row in quality["dimensions"] if row["key"] == "industry")
    cutoff = next(row["value"] for row in industry["fields"] if row["label"] == "数据截止")
    assert cutoff == "2026-11-15"

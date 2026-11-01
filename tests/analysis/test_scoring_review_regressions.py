"""审查缺陷经过实际分析、API载荷及下载事实入口的回归。"""
from datetime import date

import pytest

from analysis.config_loader import ConfigLoader
from analysis.financial import FinancialAnalyzer
from analysis.valuation import ValuationAnalyzer
from data.enrichers.valuation_enricher import ValuationEnricher
from data.schemas import (
    AnalysisContext,
    DimensionSufficiency,
    EnrichedIndustry,
    EnrichedValuation,
    FinancialData,
    IndustryData,
    SufficiencyLevel,
    ValuationData,
)
from report.builder import ReportBuilder
from report.formatter import format_metrics
from report.payload import build_stock_payload


@pytest.mark.parametrize("classification", ["Conglomerates", "未映射行业", "无财报"])
def test_hk_fallback_template_is_visible_in_actual_report_and_api(classification):
    ctx = AnalysisContext(symbol="00001.HK", name="样例", market="hk", industry_data=IndustryData(
        symbol="00001.HK", industry="综合企业", sector=classification), financial_data=[] if classification == "无财报" else [FinancialData(
            symbol="00001.HK", fiscal_quarter=date(2025, 12, 31), revenue=100, net_profit=10)])
    config = ConfigLoader().load_for_context(ctx)
    result = FinancialAnalyzer().analyze(ctx, config)
    payload = build_stock_payload(ctx.symbol, ctx.name, [result], {}, ctx, no_llm=True)
    visible = payload["dimensions"]["financial"]
    assert "通用规则" in visible["metrics"]["评分模板"]
    assert "高端制造" in visible["metrics"]["评分模板"]
    assert "通用规则" in visible["score_detail"]
    markdown = ReportBuilder().build(ctx.symbol, ctx.name, [result], {}, market="港股")
    assert "评分模板" in markdown and "通用规则" in markdown
    assert format_metrics(result.metrics)["评分模板"] == visible["metrics"]["评分模板"]


@pytest.mark.parametrize("market", ["hk", "a-shares"])
@pytest.mark.parametrize("snapshot", [None, "nonpositive", "valid"])
def test_independent_comparison_premium_survives_original_insufficient_gate(market, snapshot):
    symbol = "00700.HK" if market == "hk" else "600519"
    ctx = AnalysisContext(symbol=symbol, name="样例", market=market)
    if snapshot is not None:
        value = -1 if snapshot == "nonpositive" else 20
        ctx.valuation_data = ValuationData(symbol=symbol, date=date(2026, 10, 23), pe_ttm=value, pb=value)
    # 真正的前置充实过程先因快照或财报/历史缺失降级，再取得独立同行证据。
    ValuationEnricher().enrich(ctx)
    original_level = ctx.sufficiency.valuation.level
    ctx.enriched_industry = EnrichedIndustry(comparison={"valuation": {"source": "同批估值",
        "as_of": "2026-10-23", "metrics": [{"key": "pe_ttm", "target": 20, "median": 25,
                                             "sample_count": 5, "status": "ok"}]}})
    config = ConfigLoader().load("机械设备")
    result = ValuationAnalyzer().analyze(ctx, config)
    assert result.score == 10
    assert result.status == "partial"
    assert ctx.sufficiency.valuation.level == original_level
    assert result.metrics["target_pe_premium"] == pytest.approx(-20)
    assert "历史" in result.score_detail and "同行" in result.summary
    payload = build_stock_payload(symbol, ctx.name, [result], {}, ctx, no_llm=True)
    assert payload["dimensions"]["valuation"]["score"] == 10
    assert payload["score"]["final"] == 10
    assert payload["signal"] is None
    markdown = ReportBuilder().build(symbol, ctx.name, [result], {}, market="港股" if market == "hk" else "A 股")
    assert "同行 PE 溢价" in markdown


def test_bank_does_not_gain_score_from_independent_pe_premium():
    ctx = AnalysisContext(symbol="00005.HK", name="样例", market="hk", enriched_industry=EnrichedIndustry(
        comparison={"valuation": {"metrics": [{"key": "pe_ttm", "target": 20, "median": 25,
                                               "sample_count": 5, "status": "ok"}]}}))
    result = ValuationAnalyzer().analyze(ctx, ConfigLoader().load("银行"))
    assert result.score is None


@pytest.mark.parametrize("has_comparison", [False, True])
def test_a_insufficient_history_cannot_reuse_residual_percentiles(has_comparison):
    ctx = AnalysisContext(symbol="600519", name="样例", enriched_valuation=EnrichedValuation(
        pe_percentile=99, pb_percentile=99), valuation_data=ValuationData(
            symbol="600519", date=date(2026, 10, 23), pe_ttm=20, pb=2))
    ctx.sufficiency.valuation = DimensionSufficiency(level=SufficiencyLevel.INSUFFICIENT,
                                                    reason="本次历史与财报不足，未能形成可用序列")
    if has_comparison:
        ctx.enriched_industry = EnrichedIndustry(comparison={"valuation": {"metrics": [
            {"key": "pe_ttm", "target": 20, "median": 25, "sample_count": 5, "status": "ok"}]}})
    result = ValuationAnalyzer().analyze(ctx, ConfigLoader().load("机械设备"))
    assert result.metrics["pe_percentile"] is None
    assert result.metrics["pb_percentile"] is None
    assert result.score == (10 if has_comparison else None)

"""美股同行过滤与经营分关闭的离线回归。"""
import pytest

from analysis.industry_scoring import score_industry_comparison
from data.enrichers.us_industry_enricher import UsIndustryEnricher
from data.schemas import AnalysisContext, IndustryData


def test_ten_peers_do_not_make_unverified_revenue_sufficient():
    quotes = {f"P{i}": {"symbol": f"P{i}", "basis_verified": True, "market_cap": 100, "pe": 10 + i, "pb": 2} for i in range(10)}
    ctx = AnalysisContext(symbol="TEST", name="测试", market="us", industry_data=IndustryData(symbol="TEST", industry="系统软件", sector="", peers=list(quotes),
        comparison_snapshot={"target": {"basis_verified": True, "market_cap": 100, "pe": 15, "pb": 3}, "quotes": quotes, "as_of": "2026-11-16"}))
    UsIndustryEnricher().enrich(ctx)
    assert ctx.enriched_industry is not None
    assert ctx.enriched_industry.peer_count == 10
    assert ctx.enriched_industry.target_pe_percentile == pytest.approx(55)
    assert ctx.sufficiency.industry.level == "partial"
    assert score_industry_comparison(ctx.enriched_industry.comparison)[0] is None
    assert all(row.net_margin is None for row in ctx.enriched_industry.top_peers)


def test_target_market_cap_missing_cannot_select_comparable_peers():
    ctx = AnalysisContext(symbol="TEST", name="测试", market="us", industry_data=IndustryData(symbol="TEST", industry="综合性银行", sector="",
        comparison_snapshot={"target": {}, "quotes": {}}))
    UsIndustryEnricher().enrich(ctx)
    assert ctx.sufficiency.industry.level == "insufficient"
    assert ctx.enriched_industry is not None
    assert "净利率口径不适用" in ctx.enriched_industry.comparison["notes"]



def test_unverified_values_and_old_enrichment_do_not_leak():
    from data.schemas import EnrichedIndustry
    ctx = AnalysisContext(symbol="TEST", name="测试", market="us", enriched_industry=EnrichedIndustry(peer_count=99))
    UsIndustryEnricher().enrich(ctx)
    assert ctx.enriched_industry is None
    ctx.industry_data = IndustryData(symbol="TEST", industry="系统软件", sector="", peers=["PEER"],
        comparison_snapshot={"target": [1], "quotes": {"PEER": None}})
    UsIndustryEnricher().enrich(ctx)
    assert ctx.enriched_industry is not None
    assert ctx.enriched_industry.peer_count == 0


def test_market_cap_relaxation_and_small_sample_medians():
    quotes = {code: {"symbol": code, "basis_verified": True, "market_cap": 2, "pe": 10, "pb": 2} for code in ("AAA", "BBB", "CCC")}
    quotes["TEST"] = {"symbol": "TEST", "basis_verified": True, "market_cap": 100, "pe": 10, "pb": 2}
    ctx = AnalysisContext(symbol="TEST", name="测试", market="us", industry_data=IndustryData(symbol="TEST", industry="系统软件", sector="", peers=list(quotes),
        comparison_snapshot={"target": quotes["TEST"], "quotes": quotes}))
    UsIndustryEnricher().enrich(ctx)
    assert ctx.enriched_industry is not None
    assert ctx.enriched_industry.peer_count == 3
    assert ctx.enriched_industry.industry_median_pe is None
    assert ctx.industry_data is not None
    assert not ctx.industry_data.comparison_ready


def test_roe_ratio_and_explicit_comparison_percent_format_identically():
    from analysis.industry import IndustryAnalyzer
    from report.formatter import format_metrics

    codes = ("AAA", "BBB", "CCC", "DDD", "EEE")
    quotes = {code: {"symbol": code, "basis_verified": True, "market_cap": 100, "pe": 10, "pb": 2} for code in codes}
    ctx = AnalysisContext(symbol="TEST", name="测试", market="us", industry_data=IndustryData(
        symbol="TEST", industry="系统软件", sector="", peers=list(codes), comparison_snapshot={
            "target": {"basis_verified": True, "market_cap": 100, "pe": 10, "pb": 2}, "quotes": quotes}))
    UsIndustryEnricher().enrich(ctx)
    metrics = format_metrics(IndustryAnalyzer().analyze(ctx).metrics)
    assert metrics["top_peers"][0]["roe"] == "20.00%"
    assert metrics["comparison"]["periods"]["latest"]["metrics"][0]["target"] == "20.00%"


def test_numeric_strings_in_snapshot_are_normalized_before_comparison():
    codes = ("AAA", "BBB", "CCC", "DDD", "EEE")
    quotes = {code: {"symbol": code, "basis_verified": True, "market_cap": "100", "pe": "10", "pb": "2"} for code in codes}
    ctx = AnalysisContext(symbol="TEST", name="测试", market="us", industry_data=IndustryData(
        symbol="TEST", industry="系统软件", sector="", peers=list(codes), comparison_snapshot={
            "target": {"basis_verified": True, "market_cap": "100", "pe": "10", "pb": "2"}, "quotes": quotes}))
    UsIndustryEnricher().enrich(ctx)
    assert ctx.enriched_industry is not None
    assert ctx.enriched_industry.industry_median_pe == 10

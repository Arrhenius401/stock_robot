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
    assert "PB仅作估值比较" in "；".join(ctx.enriched_industry.comparison["notes"])



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


def test_valuation_ratio_is_not_operating_roe():
    from analysis.industry import IndustryAnalyzer
    from report.formatter import format_metrics

    codes = ("AAA", "BBB", "CCC", "DDD", "EEE")
    quotes = {code: {"symbol": code, "basis_verified": True, "market_cap": 100, "pe": 10, "pb": 2} for code in codes}
    ctx = AnalysisContext(symbol="TEST", name="测试", market="us", industry_data=IndustryData(
        symbol="TEST", industry="系统软件", sector="", peers=list(codes), comparison_snapshot={
            "target": {"basis_verified": True, "market_cap": 100, "pe": 10, "pb": 2}, "quotes": quotes}))
    UsIndustryEnricher().enrich(ctx)
    metrics = format_metrics(IndustryAnalyzer().analyze(ctx).metrics)
    assert metrics["top_peers"][0]["roe"] in (None, "N/A", "--", "—", "暂无数据")
    assert metrics["comparison"]["periods"]["latest"]["metrics"][0]["target"] != "20.00%"


def test_numeric_strings_in_snapshot_are_normalized_before_comparison():
    codes = ("AAA", "BBB", "CCC", "DDD", "EEE")
    quotes = {code: {"symbol": code, "basis_verified": True, "market_cap": "100", "pe": "10", "pb": "2"} for code in codes}
    ctx = AnalysisContext(symbol="TEST", name="测试", market="us", industry_data=IndustryData(
        symbol="TEST", industry="系统软件", sector="", peers=list(codes), comparison_snapshot={
            "target": {"basis_verified": True, "market_cap": "100", "pe": "10", "pb": "2"}, "quotes": quotes}))
    UsIndustryEnricher().enrich(ctx)
    assert ctx.enriched_industry is not None
    assert ctx.enriched_industry.industry_median_pe == 10


@pytest.mark.parametrize("count,expected", [(12, 10), (2, 2)])
def test_all_valid_peers_relaxation_preserves_sources_and_closed_score(count, expected):
    from report.peer_comparison import format_peer_comparison_markdown

    quotes = {f"P{i}": {"symbol": f"P{i}", "basis_verified": True, "market_cap": i + 1,
        "pe": 10, "pb": 2, "revenue": None,
        "source": "东财美股行情" if i % 2 else "东财美股 F10 主指标"} for i in range(count)}
    ctx = AnalysisContext(symbol="COST" if count == 2 else "AAPL", name="目标", market="us",
        industry_data=IndustryData(symbol="TEST", industry="测试行业", sector="", peers=list(quotes),
            comparison_snapshot={"target": {"basis_verified": True, "market_cap": 100000,
                "pe": 10, "pb": 2, "source": "东财美股 F10 主指标"}, "quotes": quotes}))
    UsIndustryEnricher().enrich(ctx)
    assert ctx.enriched_industry is not None
    enriched = ctx.enriched_industry
    assert enriched.peer_count == expected
    assert enriched.top_peers[0].symbol == f"P{count - 1}"
    comparison = enriched.comparison
    assert "市值分层不足，已放宽" in "；".join(comparison["notes"])
    assert comparison["valuation"]["source"] == "混合来源（逐行标注）"
    for peer in comparison["valuation"]["peers"]:
        assert peer["source"] == quotes[peer["symbol"]]["source"]
    markdown = format_peer_comparison_markdown(comparison)
    assert "| 代码 |" in markdown
    assert "东财美股 F10 主指标" in markdown and "东财美股行情" in markdown
    assert score_industry_comparison(comparison)[0] is None
    assert all(peer.net_margin is None for peer in enriched.top_peers)
    if count == 2:
        assert ctx.sufficiency.industry.level == "insufficient"
        assert enriched.industry_median_pe is None

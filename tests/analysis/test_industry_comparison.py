"""同行比较的统一证据与不计分回归。"""
from analysis.industry import IndustryAnalyzer
from data.schemas import AnalysisContext, EnrichedIndustry, IndustryData


def test_industry_has_no_independent_score_even_with_config():
    ctx = AnalysisContext(symbol="600519", name="测试", industry_data=IndustryData(
        symbol="600519", industry="白酒Ⅲ", sector="食品饮料", peer_scope="申万三级",
        peers=["000858"],
    ))
    result = IndustryAnalyzer().analyze(ctx, {"industry": {"base_score": 5}})
    assert result.score is None
    assert result.risk_flags == []
    assert "不计分" in result.score_detail


def test_industry_passes_single_comparison_evidence_without_recomputing():
    comparison = {
        "version": 1, "scope": "申万三级 · 白酒Ⅲ", "peer_count": 7,
        "valuation": {"as_of": "2026-10-23", "source": "腾讯同批快照", "metrics": [
            {"key": "pe_ttm", "status": "ok", "sample_count": 6, "target": 20,
             "median": 25, "percentile": 33.33},
        ]}, "periods": {}, "errors": [], "notes": [],
    }
    ctx = AnalysisContext(symbol="600519", name="测试", industry_data=IndustryData(
        symbol="600519", industry="白酒Ⅲ", sector="食品饮料", peer_scope="申万三级"))
    ctx.enriched_industry = EnrichedIndustry(peer_count=7, comparison=comparison)
    result = IndustryAnalyzer().analyze(ctx)
    assert result.metrics["comparison"] == comparison
    assert result.score is None
    assert result.metrics["peer_count"] == 7


def test_industry_preserves_partial_evidence_when_one_metric_is_missing():
    comparison = {"valuation": {"metrics": [
        {"key": "pe_ttm", "status": "ok", "sample_count": 6},
        {"key": "pb", "status": "insufficient", "sample_count": 2},
    ]}, "periods": {}, "errors": ["最新期经营数据获取失败"]}
    ctx = AnalysisContext(symbol="600519", name="测试", industry_data=IndustryData(
        symbol="600519", industry="白酒Ⅲ", sector="食品饮料", peer_scope="申万三级"))
    ctx.enriched_industry = EnrichedIndustry(peer_count=7, comparison=comparison)
    result = IndustryAnalyzer().analyze(ctx)
    assert result.status == "partial"
    assert result.metrics["comparison"]["errors"] == comparison["errors"]

"""指数样本无效时不得向报告和比较表泄露低估结论。"""
from datetime import date

import pytest

from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexValuationData
from index.analysis.valuation import IndexValuationAnalyzer
from index.build_compare import IndexCompareReportBuilder
from index.build_single import IndexReportBuilder, render_index_report_markdown


@pytest.mark.parametrize("pct", [10, -1, 101, float("nan"), float("inf"), None])
def test_invalid_history_cannot_vote_or_compare(pct):
    ctx = IndexAnalysisContext(target=AnalysisTarget(
        target_type="index", symbol="000300", name="沪深300", market="a-shares", index_style="broad"))
    ctx.valuation_data = IndexValuationData(symbol="000300", date=date(2026, 10, 20),
                                           pe_ttm=12.5, pe_percentile=pct, pb_percentile=pct,
                                           valuation_valid=False)
    result = IndexValuationAnalyzer().analyze(ctx)
    assert result.metrics.get("tag") == "invalid"
    assert result.metrics.get("pe_percentile") is None
    report = IndexReportBuilder().build(ctx, [result])
    assert report.tag_valuation == "invalid"
    assert report.overview["pe_percentile"] is None
    assert report.position_coeff == 0.5
    compare = IndexCompareReportBuilder().build([ctx], [report])
    assert "历史样本不足" in compare.rows[0]["pe_pct"]
    text = render_index_report_markdown(report)
    assert "分位仅供参考" not in text
    assert "12.50" in text


def test_missing_percentile_is_not_complete():
    ctx = IndexAnalysisContext(target=AnalysisTarget(
        target_type="index", symbol="000300", name="沪深300", market="a-shares", index_style="broad"))
    ctx.valuation_data = IndexValuationData(symbol="000300", date=date(2026, 10, 20), pe_ttm=12.5)
    result = IndexValuationAnalyzer().analyze(ctx)
    assert result.status == "partial"
    assert result.metrics["tag"] == "invalid"


def test_strategy_empty_snapshot_removes_stale_percentile_before_return():
    ctx = IndexAnalysisContext(target=AnalysisTarget(
        target_type="index", symbol="980092", name="现金流指数", market="a-shares", index_style="strategy"))
    ctx.valuation_data = IndexValuationData(symbol="980092", date=date(2026, 10, 20),
                                           pe_ttm=float("nan"), pe_percentile=10, valuation_valid=False)
    result = IndexValuationAnalyzer().analyze(ctx)
    assert result.status == "unavailable"
    assert "pe_percentile" not in result.metrics
    assert "pe_ttm" not in result.metrics


@pytest.mark.parametrize("style", ["broad", "strategy"])
def test_summary_does_not_show_unrounded_floats(style):
    ctx = IndexAnalysisContext(target=AnalysisTarget(
        target_type="index", symbol="000300", name="测试指数", market="a-shares", index_style=style))
    ctx.valuation_data = IndexValuationData(symbol="000300", date=date(2026, 10, 20),
                                           pe_ttm=12.345678, pe_percentile=40.123456, valuation_valid=True)
    result = IndexValuationAnalyzer().analyze(ctx)
    assert "12.35" in result.summary
    assert "40.12%" in result.summary
    assert "12.345678" not in result.summary
    assert "40.123456" not in result.summary

"""港股报告按市场展示背景维度。"""
from data.schemas import AnalysisResult, AnalysisTarget, IndexAnalysisContext
from index.build_single import IndexReportBuilder


def test_hk_report_includes_market_context_and_records_failure():
    target = AnalysisTarget(target_type="index", symbol="HSI", name="恒生指数", market="hk", index_style="overseas")
    ctx = IndexAnalysisContext(target=target)
    result = AnalysisResult(dimension="index_macro", status="ok", summary="香港利率", metrics={})
    report = IndexReportBuilder().build(ctx, [result])
    assert {"capital", "macro", "sentiment"} <= set(report.visible_sections)
    dimensions = {item["key"]: item for item in report.data_quality["dimensions"]}
    assert dimensions["macro"]["status"] == "ok"
    assert dimensions["sentiment"]["status"] == "unavailable"


def test_hk_capital_report_uses_southbound_market_context():
    from datetime import date

    from data.schemas import CapitalFlowData
    from index.analysis.capital_flow import CapitalFlowAnalyzer

    target = AnalysisTarget(target_type="index", symbol="HSI", name="恒生指数", market="hk", index_style="overseas")
    ctx = IndexAnalysisContext(target=target, capital_flow=CapitalFlowData(symbol="HSI", date=date(2026, 10, 30), southbound_sh_yi=-22.61, southbound_sz_yi=-12.98))
    report = IndexReportBuilder().build(ctx, [CapitalFlowAnalyzer().analyze(ctx)])
    assert "港股通市场背景" in report.section_capital["summary"]
    assert "北向" not in report.section_capital["summary"]

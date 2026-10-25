"""资金展示与分析判断隔离，防止报告修辞改变综合研判。"""
from datetime import date

import pytest

from data.schemas import (
    AnalysisResult,
    AnalysisTarget,
    CapitalFlowData,
    IndexAnalysisContext,
)
from index.analysis.capital_flow import CapitalFlowAnalyzer
from index.build_compare import IndexCompareReportBuilder
from index.build_single import IndexReportBuilder, render_index_report_markdown


@pytest.mark.parametrize("north,main,label", [
    (0, None, "无净流入或净流出"), (-0.0, 0, "无净流入或净流出"),
    (None, 0, "无净流入或净流出"), (None, None, "暂无数据"),
    (float("nan"), float("inf"), "暂无数据"),
    (0, 2, "流入"), (0, -2, "流出"), (0.0001, None, "流入"),
    (-0.0001, None, "流出"), (5.2, None, "流入"),
])
def test_capital_display_does_not_change_calculations(north, main, label):
    ctx = IndexAnalysisContext(target=AnalysisTarget(
        target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    ctx.capital_flow = CapitalFlowData(symbol="000300", date=date(2026, 10, 20),
                                      north_bound=north, main_net_inflow=main)
    result = CapitalFlowAnalyzer().analyze(ctx)
    original = result.model_dump()
    builder = IndexReportBuilder()
    expected_composite = builder._composite({"index_valuation": "invalid", "index_capital_flow": result.metrics["tag"]})
    report = builder.build(ctx, [result])
    assert report.section_capital["display"]["label"] == label
    assert report.tag_capital == result.metrics["tag"]
    assert (report.composite_comment, report.position_coeff) == expected_composite
    assert result.model_dump() == original
    assert report.section_capital["metrics"] == result.metrics
    markdown = render_index_report_markdown(report)
    assert f"| 资金面 | {label} |" in markdown
    if label == "无净流入或净流出":
        assert label in markdown
        assert "净流出 0.0亿" not in markdown
    if north in (0.0001, -0.0001):
        assert "0.0001 亿" in markdown
        assert "0.00 亿" not in report.section_capital["display"]["summary"]
    if label == "暂无数据" and north is None:
        assert "暂无数据" in report.section_capital["display"]["summary"]
    if label == "暂无数据" and north is not None:
        assert "数据异常" in report.section_capital["display"]["summary"]
    compare = IndexCompareReportBuilder().build([ctx], [report])
    assert compare.rows[0]["capital"] == report.tag_capital
    assert compare.rows[0]["capital_display"]["label"] == label


def test_unavailable_residual_values_are_not_reported_as_flow():
    from report.capital_display import capital_presentation

    assert capital_presentation({"status": "unavailable", "metrics": {"north_bound": 5}})["label"] == "暂无数据"
    ctx = IndexAnalysisContext(target=AnalysisTarget(
        target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    result = AnalysisResult(dimension="index_capital_flow", status="unavailable",
                            summary="旧资金残留摘要", metrics={"north_bound": 5, "tag": "positive"})
    report = IndexReportBuilder().build(ctx, [result])
    text = render_index_report_markdown(report)
    assert "资金流向：暂无数据" in text
    assert "旧资金残留摘要" not in text
    assert "净流入 5" not in text
    assert "| 资金面 | 暂无数据 |" in text


def test_cli_compare_uses_display_label():
    from io import StringIO

    from rich.console import Console

    from index.build_compare import CompareTable
    from stock_robot.cli import _render_compare_table

    table = CompareTable(headers=["指数名称", "资金"], rows=[{
        "name": "沪深300", "capital": "negative", "capital_display": {"label": "无净流入或净流出"},
    }])
    output = StringIO()
    Console(file=output, width=120).print(_render_compare_table(table))
    assert "无净流入或净流出" in output.getvalue()
    assert "negative" not in output.getvalue()

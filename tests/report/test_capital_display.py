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
    report = builder.build(ctx, [result])
    # 新版不再从资金值生成总投票标签，保留旧字段仅用于兼容。
    expected_label = label if label in {"无净流入或净流出", "暂无数据"} else "方向未记录"
    assert report.section_capital["display"]["label"] == expected_label
    assert report.position_coeff is None
    assert "不生成整体方向评级" in report.composite_comment or "方向评级" in report.composite_comment or "方向判断" in report.composite_comment
    assert result.model_dump() == original
    assert report.section_capital["metrics"] == result.metrics
    markdown = render_index_report_markdown(report)
    assert "### 资金流向" in markdown
    assert "建议仓位" not in markdown
    assert "净流出 0.0亿" not in markdown
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
    assert compare.rows[0]["capital_display"]["label"] == expected_label
    assert "资金" not in compare.headers
    assert "综合评级" not in compare.headers


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
    assert report.section_capital["display"]["rows"] == []
    assert "### 资金流向" in text


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



def test_partial_capital_preserves_unverified_source_reason_without_fabricated_flow():
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    ctx.capital_flow = CapitalFlowData(symbol="000300", date=date(2026, 10, 20),
        north_bound=None, notes=["北向源未披露真实净流量，返回零值不能证明净流量为零"])
    result = CapitalFlowAnalyzer().analyze(ctx)
    original = result.model_dump()
    report = IndexReportBuilder().build(ctx, [result])
    text = render_index_report_markdown(report)
    assert "不能证明净流量为零" in report.section_capital["summary"]
    assert "不能证明净流量为零" in str(report.data_quality["limitations"])
    assert "北向资金：暂无数据" in text
    assert "北向资金：无净流入或净流出" not in text
    assert report.section_capital["metrics"] == original["metrics"]

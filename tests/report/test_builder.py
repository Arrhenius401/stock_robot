"""报告构建冒烟：验证正常和缺失数据可渲染，不锁定可编辑模板内容。"""
import pytest

from data.schemas import AnalysisResult
from report.builder import ReportBuilder


@pytest.mark.parametrize("results", [
    [],
    [AnalysisResult(dimension="financial", status="ok", summary="财务样例", metrics={"revenue": 100, "roe": .12})],
    [AnalysisResult(dimension="financial", status="unavailable", summary="数据缺失", metrics={})],
])
def test_build_full_report(results):
    report = ReportBuilder().build("000001", "平安银行", results, {"bulk": "样例解读正文"})
    assert report.strip()
    assert "平安银行" in report and "000001" in report
    assert "样例解读正文" in report

from datetime import datetime
import pytest
from report.builder import ReportBuilder
from data.schemas import AnalysisResult


class TestReportBuilder:
    def test_build_full_report(self):
        results = [
            AnalysisResult(dimension="financial", status="ok",
                           summary="营收增长15%，ROE稳健",
                           metrics={"revenue_growth_yoy": 0.15, "roe": 0.12}),
            AnalysisResult(dimension="technical", status="ok",
                           summary="价格位于20日均线上方3.2%",
                           metrics={"latest_close": 12.5, "ma20": 12.1}),
            AnalysisResult(dimension="valuation", status="partial",
                           summary="PE(TTM) 7.50",
                           metrics={"pe_ttm": 7.5, "pb": 0.85}),
            AnalysisResult(dimension="industry", status="ok",
                           summary="所属行业: 银行",
                           metrics={"industry": "银行"}),
            AnalysisResult(dimension="sentiment", status="ok",
                           summary="近1日共 3 条相关新闻",
                           metrics={"headline_count": 3}),
        ]
        builder = ReportBuilder()
        report = builder.build("000001", "平安银行", results, commentary={
            "bulk": "综合解读内容。",
        })
        assert "# 平安银行（000001）分析报告" in report
        assert "## 一、标的基础概况" in report
        assert "## 二、五大维度量化数据" in report
        assert "## 三、五大维度标准化打分" in report
        assert "## 四、AI 中性解读 + 风险汇总" in report
        assert "## 五、多风格观察视角" in report
        assert "## 六、工具局限性 + 免责声明" in report
        assert "1. 财务量化数据" in report
        assert "2. 技术面量化数据" in report
        assert "3. 估值量化数据" in report
        assert "4. 行业对比量化数据" in report
        assert "5. 舆情量化数据" in report

    def test_partial_data_shows_warning(self):
        results = [
            AnalysisResult(dimension="financial", status="unavailable",
                           summary="财务数据不可用", metrics={}),
        ]
        builder = ReportBuilder()
        report = builder.build("000001", "平安银行", results, commentary={})
        # 数据不足的维度标注
        assert "该维度数据不足，已跳过" in report

    def test_report_includes_disclaimer(self):
        builder = ReportBuilder()
        report = builder.build("000001", "测试", [], {})
        assert "不构成任何形式的投资建议" in report

    def test_report_includes_timestamp(self):
        builder = ReportBuilder()
        report = builder.build("000001", "测试", [], {})
        now = datetime.now()
        assert str(now.year) in report

    def test_table_header_and_rows_are_contiguous(self):
        results = [
            AnalysisResult(dimension="valuation", status="partial", summary="",
                           metrics={"pe_ttm": 7.5, "pb": 0.85, "ps_ttm": 1.2}),
        ]
        report = ReportBuilder().build("600350", "山东高速", results, commentary={})
        lines = report.split("\n")
        for i, line in enumerate(lines):
            if "指标" in line and "数值" in line:
                assert lines[i + 1].startswith("|-"), f"期望分隔线，得到: {lines[i + 1]}"
                assert lines[i + 2].startswith("| "), f"期望数据行，得到: {lines[i + 2]}"
                assert "pe_ttm" in lines[i + 2]
                return
        pytest.fail("未找到表格结构")

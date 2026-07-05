from datetime import datetime
from src.report.builder import ReportBuilder
from src.data.schemas import AnalysisResult


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
            "financial": "财务表现稳健。",
            "technical": "技术面偏多。",
            "valuation": "估值合理。",
            "industry": "行业地位稳固。",
            "sentiment": "舆情偏正面。",
            "summary": "综合来看，该公司基本面扎实。",
        })
        assert "# 平安银行（000001）分析报告" in report
        assert "## 财务分析" in report
        assert "## 技术面分析" in report
        assert "## 估值分析" in report
        assert "## 行业分析" in report
        assert "## 舆情分析" in report
        assert "## 综合总结" in report
        assert "免责声明" in report

    def test_partial_data_shows_warning(self):
        results = [
            AnalysisResult(dimension="financial", status="unavailable",
                           summary="财务数据不可用", metrics={}),
        ]
        builder = ReportBuilder()
        report = builder.build("000001", "平安银行", results, commentary={
            "financial": "数据不可用。", "summary": "数据不足。"
        })
        # 清除加粗标记后检查
        assert "数据不可用" in report.replace("*", "")

    def test_report_includes_disclaimer(self):
        builder = ReportBuilder()
        report = builder.build("000001", "测试", [], {})
        assert "不构成任何投资建议" in report

    def test_report_includes_timestamp(self):
        builder = ReportBuilder()
        report = builder.build("000001", "测试", [], {})
        now = datetime.now()
        assert str(now.year) in report

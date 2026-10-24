from datetime import datetime

import pytest

from data.schemas import AnalysisResult
from report.builder import ReportBuilder


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
        assert "## 六、待验证事项" in report
        assert "## 七、数据覆盖与时效" in report
        assert "## 八、免责声明" in report
        assert [line for line in report.splitlines() if line.startswith("## ")][-1] == "## 八、免责声明"
        assert "工具局限性 + 免责声明" not in report
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
        assert "不构成投资建议或收益保证" in report
        assert "部分内容由 AI 生成" not in report
        assert "所有分析方法均为量化指标客观计算" not in report
        assert "投资决策的唯一依据" in report

    @pytest.mark.parametrize("market", ["A 股", "港股"])
    def test_disclaimer_is_last_and_preserves_existing_content(self, market):
        from data.schemas import AnalysisContext
        from report.payload import build_stock_payload

        report = ReportBuilder().build("00700.HK", "测试", [], {}, market=market)
        assert report.rfind("\n## ") == report.index("\n## 八、免责声明")
        assert report.index("## 七、数据覆盖与时效") < report.index("## 八、免责声明")
        payload = build_stock_payload("00700.HK", "测试", [], {}, AnalysisContext(
            symbol="00700.HK", name="测试", market="hk" if market == "港股" else "a-shares"))
        assert payload["disclaimer"]
        assert "部分内容由 AI 生成" not in "".join(payload["disclaimer"])
        for paragraph in payload["disclaimer"]:
            assert paragraph in report
        assert ("目录列报币种" in "".join(payload["disclaimer"])) == (market == "港股")

    def test_report_includes_timestamp(self):
        builder = ReportBuilder()
        report = builder.build("000001", "测试", [], {})
        now = datetime.now().astimezone()
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
                assert "PE(TTM)" in lines[i + 2]
                return
        pytest.fail("未找到表格结构")

    def test_metric_table_uses_chinese_display_name(self):
        results = [
            AnalysisResult(dimension="financial", status="ok", summary="",
                           metrics={"latest_quarter": "2026-06-30"}),
        ]

        report = ReportBuilder().build("000001", "平安银行", results, commentary={})

        assert "最新财报季度" in report
        assert "| latest_quarter" not in report

    def test_report_renders_peer_names_and_hides_internal_mapping(self):
        results = [
            AnalysisResult(
                dimension="industry",
                status="ok",
                summary="所属行业: 银行",
                metrics={
                    "industry": "银行",
                    "peers": ["601398", "601939"],
                    "peer_names": {"601398": "工商银行", "601939": "建设银行"},
                },
            ),
            AnalysisResult(
                dimension="sentiment",
                status="ok",
                summary="近1日共 2 条相关新闻",
                metrics={"headlines": ["业绩增长超预期", "机构上调目标价"]},
            ),
        ]

        report = ReportBuilder().build("000001", "平安银行", results, commentary={})

        assert "工商银行（601398）" in report
        assert "建设银行（601939）" in report
        assert "- 工商银行（601398）\n- 建设银行（601939）" in report
        assert "peer_names" not in report
        assert "- 业绩增长超预期" in report
        assert "- 机构上调目标价" in report

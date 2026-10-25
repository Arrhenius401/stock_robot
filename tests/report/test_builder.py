from datetime import datetime

import pytest

from data.schemas import AnalysisResult
from report.builder import ReportBuilder


class TestReportBuilder:
    @pytest.mark.parametrize("market", ["A 股", "港股"])
    def test_ai_perspectives_and_rating_are_subsections_inside_fourth_section(self, market):
        commentary = {"bulk": "\n\n".join(f"## {i}.{title}\n\n{body}" for i, title, body in [
            (1, "维度分项解读", "财务原文"), (2, "全维度风险汇总", "风险原文"),
            (3, "多风格观察视角", "价值观点；成长观点；短线观点"), (4, "综合评级", "综合评价原文")])}
        original = commentary["bulk"]
        report = ReportBuilder().build("000001", "测试", [], commentary, market=market)
        chapter = report.split("## 四、AI 综合解读", 1)[1].split("## 五、待验证事项", 1)[0]
        assert "### 3.多风格观察视角" in chapter and "### 4.综合评级" in chapter
        assert chapter.count("价值观点；成长观点；短线观点") == 1
        assert "综合评价原文" in chapter and "已包含在上方" not in report
        assert len([line for line in report.splitlines() if line.startswith("## ")]) == 7
        assert commentary["bulk"] == original

    @pytest.mark.parametrize("state", ["failed", "disabled", "insufficient"])
    def test_missing_ai_never_claims_perspectives_were_generated(self, state):
        from data.schemas import AnalysisContext, DimensionSufficiency, SufficiencyLevel
        from report.scoring import build_report

        ctx = AnalysisContext(symbol="000001", name="测试")
        results = []
        if state != "insufficient":
            ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT)
            results = [AnalysisResult(dimension="financial", status="ok", summary="财务", score=8)]
        report = build_report(ctx.symbol, ctx.name, results, {}, ctx, no_llm=state == "disabled")
        assert "## 四、AI 综合解读" in report and "## 五、多风格观察视角" not in report
        assert "已包含在上方" not in report and "3. 多风格观察视角：" not in report
        if state == "failed":
            assert "AI 解读当前不可用" in report and "本次未生成多风格观察和综合评价" in report
        else:
            assert "未启用 AI 解读" in report if state == "disabled" else "暂不生成 AI 结论" in report

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
        assert "## 三、综合评分" in report
        assert "## 四、AI 综合解读" in report
        assert "## 五、多风格观察视角" not in report
        assert "## 五、待验证事项" in report
        assert "## 六、数据覆盖与时效" in report
        assert "## 七、免责声明" in report
        assert [line for line in report.splitlines() if line.startswith("## ")][-1] == "## 七、免责声明"
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
        assert report.rfind("\n## ") == report.index("\n## 七、免责声明")
        assert report.index("## 六、数据覆盖与时效") < report.index("## 七、免责声明")
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

    def test_report_hides_peer_names_and_keeps_other_dimension_lists(self):
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

        assert "工商银行" not in report and "601398" not in report
        assert "建设银行" not in report and "601939" not in report
        assert "peer_names" not in report
        assert "- 业绩增长超预期" in report
        assert "- 机构上调目标价" in report

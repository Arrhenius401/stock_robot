"""新版指数报告展示和数据质量契约。"""
from data.schemas import AnalysisTarget, IndexAnalysisContext
from index.build_single import IndexReportBuilder, render_index_report_markdown


def test_empty_report_has_no_fabricated_judgment_or_position():
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    report = IndexReportBuilder().build(ctx, [])
    text = render_index_report_markdown(report)
    assert report.report_version == 2
    assert report.position_coeff is None
    assert "中性震荡" not in text
    assert "仓位" not in text
    for title in ["一、概览", "二、历史表现与技术面", "三、估值面", "四、资金、宏观与新闻", "五、综合研判", "六、数据覆盖与时效", "七、免责声明"]:
        assert title in text
    assert "数据范围／截至" in text
    assert report.data_quality["dimensions"]
    assert "Wilder" not in text.split("## 七、免责声明")[1]
    assert render_index_report_markdown(report) == text


def test_quality_keeps_failure_reason_and_does_not_include_ai():
    from data.schemas import AnalysisResult, DataCollectionRecord
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    ctx.collection_records["index_valuation"] = DataCollectionRecord(source="官方", error_message="HTTP 404")
    report = IndexReportBuilder().build(ctx, [AnalysisResult(dimension="index_valuation", status="unavailable", summary="请求失败", metrics={})])
    assert "HTTP 404" in str(report.data_quality["limitations"])
    assert all(row["key"] != "commentary" for row in report.data_quality["summary_rows"])
    assert "coverage" not in report.data_quality


def test_nan_snapshot_is_not_shown_as_value():
    from datetime import date

    from data.schemas import IndexValuationData
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="399006", name="创业板指", index_style="broad"))
    ctx.valuation_data = IndexValuationData(symbol="399006", date=date(2026, 9, 30), pe_snapshot=float("nan"), pb=5.2, valuation_valid=False)
    report = IndexReportBuilder().build(ctx, [])
    assert [row["label"] for row in report.section_valuation["display"]["rows"]] == ["PB"]
    assert report.section_valuation["display"]["rows"][0]["note"].startswith("2026-09-30")


def test_news_text_and_url_are_safe_and_timestamp_not_invented():
    from data.schemas import AnalysisResult
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    item = {"title": "<script>bad</script>\n## 假章节", "source": "来源", "published_at": None, "url": "javascript:alert(1)"}
    result = AnalysisResult(dimension="index_sentiment", status="partial", summary="本次取得市场新闻", metrics={"items": [item]})
    text = render_index_report_markdown(IndexReportBuilder().build(ctx, [result]))
    assert "<script>" not in text
    assert "javascript:" not in text
    assert "发布时间未记录" in text
    assert "\n## 假章节" not in text


def test_strategy_display_preserves_coverage_and_dates():
    from data.schemas import AnalysisResult
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="980092", name="现金流", index_style="strategy"))
    result = AnalysisResult(dimension="index_strategy", status="partial", summary="部分覆盖", metrics={"source": "官方", "as_of": "2026-09-30", "weight_as_of": "2026-09-29", "financial_years": [2023, 2024, 2025], "indicators": [{"label": "自由现金流收益率", "value": 2.5, "unit": "%", "coverage_pct": 65, "scope": "已覆盖样本估算"}], "industry_weights": [{"industry": "工业", "weight_pct": 20}]})
    report = IndexReportBuilder().build(ctx, [result])
    assert report.section_strategy is not None
    rows = report.section_strategy["display"]["rows"]
    assert any(row["value"] == "2026-09-29" for row in rows)
    assert any("65%" in row["note"] for row in rows)
    assert any(row["label"] == "工业" for row in rows)


def test_pb_percentile_survives_missing_pe_in_display_and_markdown():
    from datetime import date

    from data.schemas import IndexValuationData
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    ctx.valuation_data = IndexValuationData(symbol="000300", date=date(2026, 10, 28), pb=1.3, pb_percentile=25, pb_sample_count=300, valuation_valid=False)
    report = IndexReportBuilder().build(ctx, [])
    rows = report.section_valuation["display"]["rows"]
    assert any(row["label"] == "PB历史分位" and row["value"] == "25.0%" for row in rows)
    assert "PB历史分位" in render_index_report_markdown(report)
    assert not any(row["label"] == "PE历史分位" for row in rows)



import pytest


@pytest.mark.parametrize(("result_state", "cache_status", "error", "expected"), [
    ("partial", "live", "Shibor请求失败", "partial"),
    ("failed", "live", "官方源超时", "unavailable"),
    ("complete", "stale", "刷新失败", "partial"),
    ("unknown", "live", "备用源降级", "partial"),
    ("not_applicable", "unknown", "", "not_applicable"),
])
def test_quality_merges_collection_diagnostics_with_analysis(result_state, cache_status, error, expected):
    from data.schemas import AnalysisResult, DataCollectionRecord
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    ctx.collection_records["index_macro"] = DataCollectionRecord(source="官方", result_state=result_state, cache_status=cache_status, error_message=error)
    report = IndexReportBuilder().build(ctx, [AnalysisResult(dimension="index_macro", status="ok", summary="PMI50.1", metrics={"pmi": 50.1})])
    row = next(row for row in report.data_quality["summary_rows"] if row["key"] == "macro")
    assert row["status"] == expected
    if error:
        assert error in str(report.data_quality["limitations"])
    if cache_status == "stale":
        assert "使用过期缓存" in str(report.data_quality["limitations"])



def test_daily_pe_summary_does_not_call_it_a_monthly_factsheet():
    from datetime import date

    from data.schemas import IndexValuationData
    from index.analysis.valuation import IndexValuationAnalyzer
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    ctx.valuation_data = IndexValuationData(symbol="000300", date=date(2026, 10, 28), pe_snapshot=12.76, pe_snapshot_basis="官方日频PE（peg字段，口径未声明TTM）", valuation_valid=False)
    result = IndexValuationAnalyzer().analyze(ctx)
    assert "日频PE 12.76" in result.summary
    assert "单张PE" not in result.summary
    assert "日频PE 12.76" in render_index_report_markdown(IndexReportBuilder().build(ctx, [result]))


def test_adx_atr_display_rounds_without_changing_original_metrics():
    from data.schemas import AnalysisResult
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    metrics = {"adx_14": 52.40794572916243, "atr_20": 68.6354321}
    result = AnalysisResult(dimension="index_technical", status="ok", metrics=metrics, summary="技术事实")
    report = IndexReportBuilder().build(ctx, [result])
    values = {row["label"]: row["value"] for row in report.section_technical["display"]["rows"]}
    assert values["ADX(14)"] == "52.41"
    assert values["ATR(20)（点）"] == "68.64"
    assert report.section_technical["metrics"]["adx_14"] == metrics["adx_14"]

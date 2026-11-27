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
    assert report.data_quality["dimensions"]
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
    assert "（来源）" in text
    assert "发布时间未记录" not in text
    assert "\n## 假章节" not in text


def test_news_items_are_separate_markdown_paragraphs():
    from data.schemas import AnalysisResult
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    items = [{"title": "第一条", "source": "甲", "url": "https://example.com/1"},
             {"title": "第二条", "source": "乙", "url": "https://example.com/2"}]
    result = AnalysisResult(dimension="index_sentiment", status="ok", summary="新闻", metrics={"items": items})
    text = render_index_report_markdown(IndexReportBuilder().build(ctx, [result]))
    assert "\n- 第一条" in text
    assert "\n- 第二条" in text


def test_quality_only_expands_non_normal_index_statuses():
    from data.schemas import AnalysisResult
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    results = [AnalysisResult(dimension="index_technical", status="ok", summary="正常", metrics={"notes": ["正常口径说明"]}),
               AnalysisResult(dimension="index_macro", status="partial", summary="部分", metrics={"notes": ["宏观数据缺失"]})]
    quality = IndexReportBuilder().build(ctx, results).data_quality
    assert "正常口径说明" not in str(quality["limitations"])
    assert "宏观数据缺失" in str(quality["limitations"])


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

def test_index_quality_uses_shared_assessment_fields():
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    quality = IndexReportBuilder().build(ctx, []).data_quality
    assert quality["status"] == "unavailable"
    assert quality["assessment_status"] == "facts_only"
    assert quality["report_scope"] == "facts_only"
    assert quality["pending_verifications"] == list(dict.fromkeys(quality["pending_verifications"]))


def test_objective_index_does_not_claim_scoring():
    from index.presentation import build_quality

    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"), extra_analysis_enabled=False)
    quality = build_quality(ctx, {key: {"status": "ok"} for key in ("technical", "valuation", "capital", "macro")})
    assert quality["assessment_status"] == "facts_only"
    assert "综合得分" not in quality["score_label"]
    report = IndexReportBuilder().build(ctx, [])
    text = render_index_report_markdown(report)
    assert "数据状态：不可用" in text
    assert "事实报告" in text


def test_objective_valuation_summary_does_not_label_valid_pe_invalid():
    from datetime import date

    from data.schemas import IndexValuationData
    from index.analysis.valuation import IndexValuationAnalyzer

    ctx = IndexAnalysisContext(
        target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"),
        extra_analysis_enabled=False,
        valuation_data=IndexValuationData(
            symbol="000300", date=date(2026, 11, 26), pe_ttm=12.94, pb=1.26,
            pe_percentile=64.1, pe_sample_count=1211, valuation_valid=True,
            percentile_lookback_years=5, percentile_sample_start=date(2021, 11, 29),
            percentile_sample_end=date(2026, 11, 26)),
    )
    summary = IndexValuationAnalyzer().analyze(ctx).summary
    assert "PE-TTM 12.94" in summary and "64.1" in summary
    assert "PE估值" not in summary and "无效" not in summary
    ctx.extra_analysis_enabled = True
    assert "估值：" in IndexValuationAnalyzer().analyze(ctx).summary


def test_two_pe_rows_are_distinguished_by_period():
    from datetime import date

    from data.schemas import IndexValuationData

    ctx = IndexAnalysisContext(
        target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"),
        valuation_data=IndexValuationData(
            symbol="000300", date=date(2026, 11, 26), pe_ttm=12.94, pe_as_of=date(2026, 11, 26),
            pe_snapshot=13.61, pe_snapshot_as_of=date(2026, 11, 26),
            pe_snapshot_basis="官方当前PE（来源字段pe，口径未声明）"),
    )
    labels = [row["label"] for row in IndexReportBuilder().build(ctx, []).overview["display_rows"]]
    assert labels.count("PE（日频）") == 1 and labels.count("PE（快照）") == 1


def test_valuation_coverage_uses_readable_dates_and_source_names():
    from datetime import date

    from data.schemas import DataCollectionRecord, IndexValuationData
    from index.presentation import build_quality

    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="930740", name="红利低波", index_style="strategy"))
    ctx.valuation_data = IndexValuationData(
        symbol="930740", date=date(2026, 11, 26), pe_sample_count=1211, pb_sample_count=0,
        percentile_sample_start=date(2021, 11, 29), percentile_sample_end=date(2026, 11, 26),
        pe_as_of=date(2026, 11, 26), pe_snapshot_period="2026-10-30",
        pe_source_url="https://www.csindex.com.cn/csindex-home/perf/index-perf",
        pe_snapshot_source_url="https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/indices/detail/files/zh_CN/930740factsheet.pdf")
    ctx.collection_records["index_valuation"] = DataCollectionRecord(source="中证指数官网")
    quality = build_quality(ctx, {"valuation": {"status": "ok", "metrics": {}}})
    fields = {field["label"]: field["value"] for dimension in quality["dimensions"]
              if dimension["key"] == "valuation" for field in dimension["fields"]}
    assert fields["样本范围"].startswith("PE 日频样本 1211 个；PB 无日频样本")
    assert "分位区间 2021-11-29 至 2026-11-26" in fields["样本范围"]
    assert fields["数据截止"] == "2026-11-26（PE）；2026-10-30（月末快照）"
    assert "https://" not in fields["数据来源"] and "中证指数官网（指数表现、月末单张）" in fields["数据来源"]


def test_macro_coverage_uses_display_names_and_skips_release_keys():
    from datetime import date

    from data.schemas import MacroContext
    from index.presentation import build_quality

    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    ctx.macro = MacroContext(symbol="000300", fetch_date=date(2026, 11, 27),
                             indicator_dates={"pmi": "2026-10-31", "shibor_3m": "2026-11-27",
                                              "us_cpi_yoy_release": "2026-11-15"})
    quality = build_quality(ctx, {"macro": {"status": "ok", "metrics": {}}})
    fields = {field["label"]: field["value"] for dimension in quality["dimensions"]
              if dimension["key"] == "macro" for field in dimension["fields"]}
    assert fields["数据截止"] == "PMI 2026-10-31；Shibor3M 2026-11-27"
    assert "_release" not in fields["数据截止"]


def test_risk_duplicate_of_limitation_is_shown_once():
    from data.schemas import AnalysisResult

    note = "官方权重较旧：2026-10-30，使用最新公开权重而非当日成分"
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="980092", name="现金流", index_style="strategy"),
                               risk_flags=[note])
    result = AnalysisResult(dimension="index_strategy", status="partial", summary="部分覆盖", metrics={"notes": [note]})
    report = IndexReportBuilder().build(ctx, [result])
    assert note in str(report.data_quality["limitations"])
    assert report.risk_list == []


def test_markdown_labels_insufficient_history_windows():
    from data.schemas import AnalysisResult

    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="930740", name="红利低波", index_style="strategy"),
                               extra_analysis_enabled=False)
    window = {"label": "近60个月", "start_date": "2025-10-23", "end_date": "2026-11-26",
              "sample_count": 267, "return_pct": 2.69, "max_drawdown_pct": -14.55,
              "annualized_volatility_pct": 11.97, "notes": ["历史不足完整窗口"]}
    result = AnalysisResult(dimension="index_performance", status="ok", summary="已取得可用数据", metrics={"windows": [window]})
    text = render_index_report_markdown(IndexReportBuilder().build(ctx, [result]))
    assert "近60个月历史不足完整窗口，按可得 267 个交易日计算（2025-10-23 至 2026-11-26）" in text
    assert "已取得可用数据" not in text


def test_strategy_methodology_only_explains_relevant_metrics():
    from index.analysis.strategy import strategy_methodology

    dividend = strategy_methodology("dividend")
    assert "股息率=" in dividend
    assert "自由现金流=" not in dividend and "参考企业价值" not in dividend
    cash = strategy_methodology("free_cash_flow")
    assert "自由现金流=" in cash and "股息率=" not in cash


def test_news_without_timestamp_omits_placeholder_but_keeps_source():
    from data.schemas import AnalysisResult

    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    items = [{"title": "标题", "source": "来源", "url": "https://example.com/a"},
             {"title": "无来源", "source": "未记录", "url": "https://example.com/b"}]
    result = AnalysisResult(dimension="index_sentiment", status="ok", summary="新闻", metrics={"items": items})
    text = render_index_report_markdown(IndexReportBuilder().build(ctx, [result]))
    assert "- 标题（来源）" in text
    assert "- 无来源" in text and "（未记录）" not in text
    assert "发布时间未记录" not in text

from report.table_notes import prepare_table, valuation_window_notes


def test_ps_source_reaches_valuation_table():
    view = prepare_table("valuation", {
        "ps_ttm": 10.3,
        "valuation_details": ["PS(TTM)：东财日频估值历史-市销率（口径未声明），日期 2026-11-23；仅披露，不计入评分"],
    })
    assert "PS(TTM)" in str(view["metrics"])
    assert any("东财" in note for note in view["notes"])


def test_eastmoney_long_window_note_is_kept():
    notes = valuation_window_notes(
        "估值数据截至 2026-11-20；PE、PB分位区间：2025-01-01 至 2026-11-20，300个有效样本；东财日频估值历史（近五年）")
    assert any("东财" in note for note in notes)


def test_verified_hk_currency_reason_reaches_financial_table():
    view = prepare_table("financial", {
        "amount_basis": "金额币种：HKD（仅代表已核实的利润表金额）",
        "amount_currency_reason": "逐期报表聚合与分析指标一致",
    })
    text = str(view["notes"])
    assert "HKD" in text and "逐期" in text


def test_brk_sec_revenue_is_display_only_row():
    view = prepare_table("financial", {
        "reported_revenue": 74322000000,
        "reported_revenue_ttm": 290000000000,
        "reported_revenue_basis": "营业收入（不含投资损益）",
        "reported_revenue_source": "SEC XBRL RevenueFromContractWithCustomerExcludingAssessedTax（10-Q，filed 2026-11-09）",
    })
    text = str(view["metrics"])
    assert "营业收入（SEC，不含投资损益，单季）" in text
    assert "营业收入（SEC，不含投资损益，TTM）" in text


def test_us_empty_news_has_specific_quality_reason():
    from data.schemas import AnalysisContext, AnalysisResult
    from report.quality import build_data_quality
    context = AnalysisContext(symbol="BRK.B", name="伯克希尔", market="us", commentary_version=2)
    context.extra_analysis_enabled = False
    quality = build_data_quality(context, [AnalysisResult(dimension="sentiment", status="unavailable", summary="无相关新闻", metrics={})])
    limitations = [row for row in quality["limitations"] if row["dimension"] == "舆情"]
    assert limitations and all(row["value"] != "未记录" for row in limitations)
    assert any("新闻" in row["value"] for row in limitations)


def test_short_history_fact_report_keeps_technical_missing_reason():
    from data.schemas import (
        AnalysisContext,
        AnalysisResult,
        DimensionSufficiency,
        SufficiencyLevel,
    )
    from report.quality import build_data_quality
    context = AnalysisContext(symbol="ANT", name="短历史样例", market="us", extra_analysis_enabled=False)
    context.sufficiency.price = DimensionSufficiency(level=SufficiencyLevel.INSUFFICIENT,
        reason="仅3条日线，不足20条，不计算技术指标", sample_count=3)
    result = AnalysisResult(dimension="technical", status="unavailable", summary="日线样本不足20条", metrics={})
    quality = build_data_quality(context, [result])
    limitations = [row for row in quality["limitations"] if row["dimension"] == "技术"]
    assert any("不足20" in row["value"] for row in limitations)

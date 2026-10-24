"""报告数据依据与输出门槛回归。"""
from datetime import UTC

from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    DimensionSufficiency,
    SufficiencyLevel,
)
from report.scoring import build_report, compute_score_summary
from report.signal import SignalConfig


def test_all_missing_has_no_score_signal_or_model_conclusion():
    ctx = AnalysisContext(symbol="000001", name="测试")
    results = [AnalysisResult(dimension="valuation", status="unavailable", summary="历史不足", metrics={"pb": 1.2})]
    text = build_report(ctx.symbol, ctx.name, results, {"bulk": "不应输出模型结论"}, ctx, signal_cfg=SignalConfig())
    assert compute_score_summary(results).final_score is None
    assert "最终综合得分：** 暂无" in text
    assert "操作信号：" not in text
    assert "不应输出模型结论" not in text
    assert "1.2" in text
    assert "待验证事项" in text
    assert text.index("## 七、数据覆盖与时效") < text.index("## 八、免责声明")


def test_coverage_conservative_fixed_weights_and_unknown_source():
    from report.quality import build_data_quality
    ctx = AnalysisContext(symbol="000001", name="测试")
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=6)
    ctx.sufficiency.price = DimensionSufficiency(level=SufficiencyLevel.PARTIAL, sample_count=30, reason="不足60条")
    results = [AnalysisResult(dimension="financial", status="ok", summary="财务", score=8),
               AnalysisResult(dimension="technical", status="partial", summary="技术", score=6)]
    quality = build_data_quality(ctx, results)
    assert quality["coverage"] == 40
    assert not quality["signal_allowed"]
    assert quality["has_scored_data"]
    financial = quality["dimensions"][0]
    assert next(f["value"] for f in financial["fields"] if f["label"] == "数据来源") == "未记录"
    assert next(f["value"] for f in quality["dimensions"][3]["fields"] if f["label"] == "数据截止") == "未记录"


def test_result_failure_overrules_sufficient_input():
    from report.quality import build_data_quality
    ctx = AnalysisContext(symbol="000001", name="测试")
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=6)
    r = AnalysisResult(dimension="financial", status="unavailable", summary="分析不可用", score=8)
    q = build_data_quality(ctx, [r])
    assert q["coverage"] == 0
    assert not q["has_scored_data"]
    assert q["dimensions"][0]["status"] == "unavailable"


def test_collection_facts_are_not_generation_time():
    from datetime import date, datetime

    from data.schemas import DataCollectionRecord, FinancialData
    from report.quality import build_data_quality

    ctx = AnalysisContext(symbol="000001", name="测试", financial_data=[FinancialData(symbol="000001", fiscal_quarter=date(2025, 12, 31))])
    ctx.collection_records["financial"] = DataCollectionRecord(source="公开财务接口", cache_status="cache", collected_at=datetime(2026, 1, 5, tzinfo=UTC))
    q = build_data_quality(ctx, [])
    fields = {f["label"]: f["value"] for f in q["dimensions"][0]["fields"]}
    assert fields["数据来源"] == "公开财务接口"
    assert fields["数据截止"] == "2025-12-31"
    assert fields["样本范围"] == "1期财报"
    assert "2026-01-05" in fields["缓存状态"]


def test_price_dates_sorted_and_actual_interval_in_markdown():
    from datetime import date

    from data.schemas import PriceData
    from report.scoring import compute_price_info

    ctx = AnalysisContext(symbol="000001", name="测试", price_data=[
        PriceData(symbol="000001", trade_date=date(2026, 2, 3), open=1, high=2, low=1, close=2, volume=1),
        PriceData(symbol="000001", trade_date=date(2026, 2, 2), open=1, high=1, low=1, close=1, volume=1),
    ])
    info = compute_price_info(ctx)
    assert info["price_start"] == "2026-02-02"
    assert info["price_end"] == "2026-02-03"
    assert info["latest_price"] == 2
    text = build_report(ctx.symbol, ctx.name, [], {}, ctx, no_llm=True)
    assert "采集区间最高价" in text
    assert "近1年最高价" not in text
    assert "2026-02-02 至 2026-02-03" in text


def test_partial_report_has_no_signal_even_with_high_score():
    ctx = AnalysisContext(symbol="000001", name="测试")
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=6)
    r = AnalysisResult(dimension="financial", status="ok", summary="财务", score=9)
    text = build_report(ctx.symbol, ctx.name, [r], {}, ctx, signal_cfg=SignalConfig())
    assert "操作信号：" not in text
    assert "暂无操作信号和仓位建议" in text
    assert "AI 解读当前不可用" in text


def test_valuation_counts_only_valid_points_and_preserves_dependencies():
    from datetime import date

    from data.schemas import (
        DailyValuationPoint,
        DataCollectionRecord,
        EnrichedValuation,
    )
    from report.quality import build_data_quality

    ctx = AnalysisContext(symbol="000001", name="测试", enriched_valuation=EnrichedValuation(daily_points=[
        DailyValuationPoint(trade_date=date(2026, 1, 2), close=2, pe=10),
        DailyValuationPoint(trade_date=date(2026, 1, 3), close=2),
    ], validated=True))
    ctx.collection_records = {key: DataCollectionRecord(source=value) for key, value in
                              {"price": "行情接口", "financial": "财务接口", "valuation": "快照接口"}.items()}
    q = build_data_quality(ctx, [])
    fields = {f["label"]: f["value"] for f in q["dimensions"][2]["fields"]}
    assert fields["数据来源"] == "行情与财务派生计算"
    assert "1个估值样本" in fields["样本范围"]
    assert fields["数据截止"] == "2026-01-02"
    assert all(source in fields["完整来源"] for source in ("行情接口", "财务接口", "快照接口"))


def test_news_fetch_day_is_not_publish_day_and_industry_unknown_is_pending():
    from datetime import date

    from data.schemas import NewsData
    from report.quality import build_data_quality

    ctx = AnalysisContext(symbol="000001", name="测试", news_data=NewsData(symbol="000001", date=date(2026, 1, 2), headlines=["新闻"]))
    q = build_data_quality(ctx, [])
    fields = {f["label"]: f["value"] for f in q["dimensions"][4]["fields"]}
    assert fields["数据截止"] == "未记录"
    assert any("行业：数据截止未记录" in item for item in q["pending_verifications"])

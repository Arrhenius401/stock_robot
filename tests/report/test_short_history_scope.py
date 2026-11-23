"""短行情只描述实际样本，不推断上市时间。"""
from datetime import date, timedelta
from typing import Literal

import pytest

from data.schemas import AnalysisContext, PriceData
from report.scoring import compute_price_info


@pytest.mark.parametrize("count", [0, 1, 2, 19, 20, 34, 59, 60])
def test_price_position_requires_twenty_bars(count):
    rows = [PriceData(symbol="ANT", trade_date=date(2026, 1, 1) + timedelta(days=i),
                      open=10, high=12, low=8, close=11, volume=100)
            for i in range(count)]
    price = compute_price_info(AnalysisContext(symbol="ANT", name="目标", market="us", price_data=rows))
    if count < 20:
        assert price["price_position"] == f"样本不足（{count} 个交易日），不计算"
    else:
        assert price["price_position"] == "75%"
    assert price["price_bars"] == count


@pytest.mark.parametrize("count", [0, 1, 2, 19, 20, 34, 59, 60])
@pytest.mark.parametrize("quote_available", [False, True])
@pytest.mark.parametrize("market,symbol", [("a-shares", "600519"), ("hk", "00700.HK"), ("us", "ANT")])
def test_real_technical_pipeline_preserves_facts_without_composite(mocker, tmp_path, count, quote_available, market, symbol):
    from analysis.technical import TechnicalAnalyzer
    from core.pipeline import Pipeline
    from core.registry import Registry
    from report.payload import build_stock_payload
    from report.scoring import build_report
    from utils.config import Config

    registry = Registry()
    registry.register_analysis_module(TechnicalAnalyzer())
    config = Config(tmp_path)
    config.data["analysis"]["extra_enabled"] = True
    pipeline = Pipeline(registry, config, llm_enabled=False)
    rows = [PriceData(symbol=symbol, trade_date=date(2026, 1, 1) + timedelta(days=i),
                      open=10, high=12, low=8, close=11, volume=100)
            for i in range(count)]
    ctx = AnalysisContext(symbol=symbol, name="目标", market=market, price_data=rows)
    # 只隔离采集和公开报价，充实、分析与两种报告均执行真实路径。
    mocker.patch.object(pipeline, "collect", return_value=ctx)
    mocker.patch("data.peer_comparison.PeerComparisonService.enrich", side_effect=lambda value, **kwargs: value)
    mocker.patch("data.business_profile.load_business_profile", return_value={})
    quote = {"status": "ok", "symbol": symbol, "price": 13, "source": "已校验报价",
             "quoted_at": "2026-11-20T16:00:00-05:00"} if quote_available else {"status": "unavailable"}
    mocker.patch("data.current_quote.collect_current_quote", return_value=quote)
    results, commentary, ctx = pipeline.run(symbol, "目标", market=market, dimension="technical")
    assert results[0].status == ("unavailable" if count < 20 else "partial" if count < 60 else "ok")
    payload = build_stock_payload(symbol, "目标", results, commentary, ctx, no_llm=True)
    assert payload["report_scope"] == "facts_only"
    assert payload["assessment_status"] == "facts_only"
    assert payload["score"]["final"] is None
    assert payload["signal"] is None
    assert payload["overview"]["year_high"] is None
    assert "MA" not in payload["commentary"] and "MACD" not in payload["commentary"]
    assert "technical" not in payload.get("research_commentary", {}).get("evidence", {})
    if quote_available:
        assert payload["overview"]["current_price"]["value"] == 13
    markdown = build_report(symbol, "目标", results, commentary, ctx, no_llm=True)
    assert "报告范围：事实报告" in markdown
    assert "新上市" not in markdown
    assert "MA60" not in markdown and "MACD" not in markdown


def test_actual_collection_preserves_backup_failure_and_cached_diagnostics(mocker, tmp_path):
    from core.pipeline import Pipeline
    from core.registry import Registry
    from data.akshare import AkShareAdapter
    from utils.config import Config

    registry = Registry()
    registry.register_data_source(AkShareAdapter())
    pipeline = Pipeline(registry, Config(tmp_path), llm_enabled=False)
    rows = [PriceData(symbol="ANT", trade_date=date(2026, 11, 19), open=10, high=12, low=8, close=11, volume=100)]
    primary = mocker.patch("data.us_stock._sina_history", return_value=rows)
    mocker.patch("data.us_stock._eastmoney_history", side_effect=ConnectionError("隔离的备源失败"))
    first = pipeline.collect("ANT", "目标", market="us", data_types=["price"])
    sources = first.collection_records["price"].sources
    assert sources[0]["rows"] == 1
    assert sources[1]["error"] == "ConnectionError"
    assert first.price_data == rows
    second = pipeline.collect("ANT", "目标", market="us", data_types=["price"])
    assert primary.call_count == 1
    assert second.collection_records["price"].cache_status == "cache"
    assert second.collection_records["price"].sources == sources


def test_financial_only_real_pipeline_produces_fact_report(mocker, tmp_path):
    from analysis.financial import FinancialAnalyzer
    from core.pipeline import Pipeline
    from core.registry import Registry
    from data.schemas import FinancialData
    from report.payload import build_stock_payload
    from report.quality import has_usable_facts
    from utils.config import Config

    registry = Registry()
    registry.register_analysis_module(FinancialAnalyzer())
    pipeline = Pipeline(registry, Config(tmp_path), llm_enabled=False)
    ctx = AnalysisContext(symbol="ANT", name="目标", market="us", financial_data=[
        FinancialData(symbol="ANT", fiscal_quarter=date(2026, 9, 30), revenue=100, net_profit=10)])
    mocker.patch.object(pipeline, "collect", return_value=ctx)
    mocker.patch("data.current_quote.collect_current_quote", return_value={"status": "unavailable"})
    results, commentary, ctx = pipeline.run("ANT", "目标", market="us", dimension="financial")
    payload = build_stock_payload("ANT", "目标", results, commentary, ctx, no_llm=True)
    assert has_usable_facts(ctx)
    assert payload["report_scope"] == "facts_only"
    assert payload["score"]["final"] is None and payload["signal"] is None
    assert payload["dimensions"]["financial"]["metrics"]["revenue"] == 100


def test_stale_price_record_survives_real_pipeline_and_report(mocker, tmp_path):
    from analysis.technical import TechnicalAnalyzer
    from core.pipeline import Pipeline
    from core.registry import Registry
    from data.schemas import DataCollectionRecord
    from report.payload import build_stock_payload
    from utils.config import Config

    registry = Registry()
    registry.register_analysis_module(TechnicalAnalyzer())
    pipeline = Pipeline(registry, Config(tmp_path), llm_enabled=False)
    ctx = AnalysisContext(symbol="ANT", name="目标", market="us", price_data=[
        PriceData(symbol="ANT", trade_date=date(2026, 11, 19), open=10, high=12, low=8, close=11, volume=100)],
        collection_records={"price": DataCollectionRecord(cache_status="stale", source="旧缓存",
            error_kind="live_unavailable", error_message="本次源失败", sources=[
                {"source": "新浪", "status": "failed", "rows": 0, "error": "TimeoutError"}])})
    mocker.patch.object(pipeline, "collect", return_value=ctx)
    mocker.patch("data.current_quote.collect_current_quote", return_value={"status": "unavailable"})
    results, commentary, ctx = pipeline.run("ANT", "目标", market="us", dimension="technical")
    payload = build_stock_payload("ANT", "目标", results, commentary, ctx, no_llm=True)
    assert payload["price"]["cache_status"] == "stale"
    assert payload["price"]["sources"][0]["error"] == "TimeoutError"
    assert "过期缓存" in str(payload["data_quality"]["limitations"])
    assert payload["signal"] is None


@pytest.mark.parametrize("count,scored,identity,expected", [
    (0, 4, "confirmed", "facts_only"), (2, 2, "confirmed", "partial"),
    (19, 1, "confirmed", "facts_only"), (20, 4, "confirmed", "partial"),
    (60, 4, "confirmed", "complete"), (60, 4, "probable", "facts_only"),
])
def test_required_dimensions_ignore_uncollected_sentiment(count, scored, identity, expected):
    from data.schemas import AnalysisResult, DimensionSufficiency, SufficiencyLevel
    from report.quality import build_data_quality

    ctx = AnalysisContext(symbol="ANT", name="目标", market="us", identity_status=identity,
        price_data=[PriceData(symbol="ANT", trade_date=date(2026, 1, 1) + timedelta(days=i),
                             open=10, high=12, low=8, close=11, volume=100) for i in range(count)])
    results = []
    dimensions: list[Literal["financial", "technical", "valuation", "industry"]] = [
        "financial", "technical", "valuation", "industry"]
    for dimension in dimensions[:scored]:
        setattr(ctx.sufficiency, "price" if dimension == "technical" else dimension,
                DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=60))
        results.append(AnalysisResult(dimension=dimension, status="ok", score=7, summary="已验证事实"))
    quality = build_data_quality(ctx, results)
    assert quality["assessment_status"] == expected
    assert len(quality["required_dimensions"]) == 4
    assert len(quality["scored_required_dimensions"]) == scored
    if expected != "complete":
        assert not quality["signal_allowed"]

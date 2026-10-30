from datetime import date

import pytest

from index.hk_official import parse_history, parse_hkma


@pytest.fixture(autouse=True)
def offline_collector_sources(monkeypatch):
    from data.collection_state import ValueResult, make_record
    from index.collector import IndexDataCollector

    monkeypatch.setattr(IndexDataCollector, "_fetch_hk_pe", lambda *a: ValueResult(None, make_record("恒生指数", "failed")))
    monkeypatch.setattr(IndexDataCollector, "_fetch_hsi_official", lambda *a: ValueResult(None, make_record("恒生官方历史", "failed")))
    monkeypatch.setattr(IndexDataCollector, "_fetch_hk_macro", lambda *a: ValueResult(None, make_record("香港金管局", "failed")))
    monkeypatch.setattr("index.hk_southbound.fetch_southbound", lambda *a, **k: ValueResult(None, make_record("东方财富港股通", "failed")))


def test_history_identity_dates_and_duplicate_conflicts():
    payload = {"code": "0", "data": [{"indexCode": "01067.00", "valueHistory": [
        {"date": "2026-10-28", "close": "1213.41"},
        {"date": "2026-10-29", "close": "1215.98"},
    ]}]}
    assert parse_history(payload, "01067.00")[-1] == (date(2026, 10, 29), 1215.98)
    with pytest.raises(ValueError):
        parse_history(payload, "00001.00")
    payload["data"][0]["valueHistory"].append({"date": "2026-10-29", "close": "10"})
    with pytest.raises(ValueError):
        parse_history(payload, "01067.00")


def test_hkma_freshness_and_values():
    payload = {"header": {"success": True}, "result": {"records": [
        {"end_of_date": "2026-10-29", "hibor_fixing_1m": 2.71494,
         "disc_win_base_rate": 4.25, "closing_balance": 53999}]}}
    row = parse_hkma(payload, date(2026, 10, 30), max_age_days=10)
    assert row["date"] == date(2026, 10, 29)
    assert row["hibor_1m"] == 2.71494
    with pytest.raises(ValueError):
        parse_hkma(payload, date(2026, 12, 1), max_age_days=10)


def test_provider_cache_and_failed_refresh(tmp_path):
    from data.cache import CacheManager
    from index.hk_official import HongKongOfficialProvider

    payload = {"code": "0", "data": [{"indexCode": "01067.00", "valueHistory": [
        {"date": "2026-10-29", "close": "1215.98"}]}]}
    calls = []

    def fetch(url, params):
        calls.append((url, params))
        if len(calls) > 1:
            raise OSError("网络断开")
        return payload

    provider = HongKongOfficialProvider(CacheManager(tmp_path / "cache.db"), fetch)
    assert provider.history("01067.00").record.cache_status == "live"
    assert provider.history("01067.00").record.cache_status == "cache"
    stale = provider.history("01067.00", refresh=True)
    assert stale.record.cache_status == "stale"
    assert stale.value == [(date(2026, 10, 29), 1215.98)]
    assert stale.errors



def test_close_only_preserves_stock_contract_and_disables_range_metrics():
    from datetime import timedelta

    from pydantic import ValidationError

    from data.schemas import (
        AnalysisTarget,
        IndexAnalysisContext,
        IndexClosePriceData,
        IndexPriceData,
        PriceData,
    )
    from index.analysis.performance import IndexPerformanceAnalyzer
    from index.analysis.technical import IndexTechnicalAnalyzer

    with pytest.raises(ValidationError):
        PriceData.model_validate({"symbol": "HSI", "trade_date": date(2026, 1, 1), "close": 100})
    rows: list[IndexPriceData | IndexClosePriceData] = [IndexClosePriceData(symbol="HSI", trade_date=date(2026, 1, 1) + timedelta(days=i), close=100 + i) for i in range(70)]
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="HSI", name="恒指", market="hk", index_style="overseas"), price_data=rows)
    technical = IndexTechnicalAnalyzer().analyze(ctx)
    assert technical.metrics["ma_20"] is not None
    assert technical.metrics["atr_20"] is None and technical.metrics["adx_14"] is None
    assert technical.status == "partial"
    assert IndexPerformanceAnalyzer().analyze(ctx).metrics["return_pct"] is not None


def test_collector_official_fallback_keeps_identity_and_checkpoint(monkeypatch, tmp_path):
    from data.base import CollectionResult
    from data.collection_state import ValueResult, make_record
    from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexClosePriceData
    from index.collector import IndexDataCollector

    collector = IndexDataCollector(isolate_requests=False, cache_requests=False)
    collector.checkpoint_path = tmp_path / "context.json"
    monkeypatch.setattr(collector, "_fetch_result", lambda *a, **k: CollectionResult([]))
    monkeypatch.setattr(collector, "_fetch_hk_macro", lambda: ValueResult(None, make_record("香港金管局", "failed")))
    seen = []
    def official(code):
        seen.append(code)
        return ValueResult([(date(2026, 10, 29), 1215.98)], make_record("恒生官方历史", "complete"))
    monkeypatch.setattr(collector, "_fetch_hsi_official", official, raising=False)
    target = AnalysisTarget(target_type="index", symbol="HSHDYI", name="恒生高股息", market="hk", index_style="overseas", official_index_code="01067.00")
    result = collector.collect(target)
    assert collector.checkpoint_path is not None
    restored = IndexAnalysisContext.model_validate_json(collector.checkpoint_path.read_text(encoding="utf-8"))
    assert seen == ["01067.00"]
    assert isinstance(result.price_data[0], IndexClosePriceData)
    assert isinstance(restored.price_data[0], IndexClosePriceData)
    assert not hasattr(restored.price_data[0], "high")


def test_collector_does_not_guess_unmapped_code(monkeypatch):
    from data.base import CollectionResult
    from data.collection_state import ValueResult, make_record
    from data.schemas import AnalysisTarget
    from index.collector import IndexDataCollector
    collector = IndexDataCollector(isolate_requests=False, cache_requests=False)
    monkeypatch.setattr(collector, "_fetch_result", lambda *a, **k: CollectionResult([]))
    monkeypatch.setattr(collector, "_fetch_hk_macro", lambda: ValueResult(None, make_record("香港金管局", "failed")))
    monkeypatch.setattr(collector, "_fetch_hsi_official", lambda code: pytest.fail("未知代码不得请求"), raising=False)
    collector.collect(AnalysisTarget(target_type="index", symbol="UNKNOWN", name="未知", market="hk", index_style="overseas"))


def test_hk_macro_analysis_uses_local_fields_only(monkeypatch):
    from data.base import CollectionResult
    from data.collection_state import ValueResult, make_record
    from data.schemas import AnalysisTarget
    from index.analysis.macro import MacroAnalyzer
    from index.collector import IndexDataCollector

    collector = IndexDataCollector(isolate_requests=False, cache_requests=False)
    monkeypatch.setattr(collector, "_fetch_result", lambda *a, **k: CollectionResult([]))
    monkeypatch.setattr(collector, "_fetch_hk_macro", lambda: ValueResult(
        {"date": date(2026, 10, 29), "hibor_1m": 2.7, "base_rate": 4.25,
         "closing_balance_hkd_million": 53999.0}, make_record("香港金管局", "complete")), raising=False)
    target = AnalysisTarget(target_type="index", symbol="HSI", name="恒指", market="hk", index_style="overseas")
    ctx = collector.collect(target)
    assert ctx.macro is not None
    assert ctx.macro.hibor_1m == 2.7
    assert ctx.macro.shibor_3m is None and ctx.macro.pmi is None
    assert ctx.collection_records["index_macro"].source == "香港金管局"
    result = MacroAnalyzer().analyze(ctx)
    assert result.metrics["hibor_1m"] == 2.7
    assert "Shibor" not in result.summary


def test_close_only_survives_real_subprocess_roundtrip():
    import os
    import subprocess
    import sys

    from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexClosePriceData

    target = AnalysisTarget(target_type="index", symbol="HSI", name="恒指", market="hk", index_style="overseas", official_index_code="00001.00")
    context = IndexAnalysisContext(target=target, price_data=[IndexClosePriceData(symbol="HSI", trade_date=date(2026, 10, 29), close=22958.65)])
    script = "import sys; from data.schemas import IndexAnalysisContext; x=IndexAnalysisContext.model_validate_json(sys.stdin.read()); print(x.model_dump_json())"
    process = subprocess.run([sys.executable, "-c", script], input=context.model_dump_json(),
                             text=True, encoding="utf-8", capture_output=True, env=os.environ.copy(), timeout=15, check=True)
    restored = IndexAnalysisContext.model_validate_json(process.stdout)
    assert isinstance(restored.price_data[0], IndexClosePriceData)
    assert not hasattr(restored.price_data[0], "high")
    assert restored.target.official_index_code == "00001.00"


@pytest.mark.parametrize("style", ["sector", "strategy", "broad"])
def test_hk_never_queries_a_share_prices_or_valuation(monkeypatch, style):
    from data.base import CollectionResult
    from data.collection_state import ValueResult, make_record
    from data.schemas import AnalysisTarget
    from index.collector import IndexDataCollector

    collector = IndexDataCollector(isolate_requests=False, cache_requests=False)
    calls = []
    def fetch(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return CollectionResult([])
    monkeypatch.setattr(collector, "_fetch_result", fetch)
    monkeypatch.setattr(collector, "_fetch_hk_macro", lambda: ValueResult(None, make_record("香港金管局", "failed")))
    monkeypatch.setattr(collector, "_fetch_hsi_official", lambda code: ValueResult([], make_record("恒生官方历史", "failed")))
    target = AnalysisTarget(target_type="index", symbol="000300", name="港股样本", market="hk", index_style=style, official_index_code="01067.00")
    collector.collect(target)
    assert not any(k["data_type"] in ("index_price", "index_valuation") for _, k in calls)


def test_known_hk_symbol_uses_overseas_route_and_never_a_valuation(monkeypatch):
    from data.base import CollectionResult
    from data.collection_state import ValueResult, make_record
    from data.schemas import AnalysisTarget
    from index.collector import IndexDataCollector

    collector = IndexDataCollector(isolate_requests=False, cache_requests=False)
    calls = []
    def fetch(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return CollectionResult([])
    monkeypatch.setattr(collector, "_fetch_result", fetch)
    monkeypatch.setattr(collector, "_fetch_hk_macro", lambda: ValueResult(None, make_record("香港金管局", "failed")))
    collector.collect(AnalysisTarget(target_type="index", symbol="HSI", name="恒指", market="hk", index_style="sector"))
    assert calls == [("HSI", {"data_type": "index_price", "index_style": "overseas"})]


def test_southbound_rejects_expired_missing_and_northbound():
    from index.hk_southbound import parse_southbound
    rows = [
        {"交易日": date(2026, 10, 29), "资金方向": "南向", "板块": "港股通(沪)", "交易状态": "3", "成交净买额": -2.0},
        {"交易日": date(2026, 10, 29), "资金方向": "南向", "板块": "港股通(深)", "交易状态": "3", "成交净买额": 3.0},
        {"交易日": date(2026, 10, 29), "资金方向": "北向", "板块": "沪股通", "交易状态": "3", "成交净买额": 1000},
    ]
    data = parse_southbound(rows, date(2026, 10, 30), max_age_days=10)
    assert data["date"] == date(2026, 10, 29)
    assert data["sh"] == -2 and data["sz"] == 3
    with pytest.raises(ValueError):
        parse_southbound(rows, date(2026, 12, 1), max_age_days=10)
    with pytest.raises(ValueError):
        parse_southbound(rows[:1], date(2026, 10, 30), max_age_days=10)


def test_hk_capital_summary_never_claims_index_flow():
    from data.schemas import AnalysisTarget, CapitalFlowData, IndexAnalysisContext
    from index.analysis.capital_flow import CapitalFlowAnalyzer
    target = AnalysisTarget(target_type="index", symbol="HSI", name="恒指", market="hk", index_style="overseas")
    context = IndexAnalysisContext(target=target, capital_flow=CapitalFlowData(
        symbol="HSI", date=date(2026, 10, 29), southbound_sh_yi=-2, southbound_sz_yi=3))
    result = CapitalFlowAnalyzer().analyze(context)
    assert "市场背景" in result.summary and "亿元" in result.summary
    assert "指数资金流" not in result.summary
    assert result.status == "ok"


def test_southbound_collector_and_news_failure(monkeypatch):
    from data.base import CollectionResult
    from data.collection_state import ValueResult, make_record
    from data.schemas import AnalysisTarget
    from index.collector import IndexDataCollector

    monkeypatch.setattr("index.hk_southbound.fetch_southbound", lambda *args, **kwargs: ValueResult(
        {"date": date(2026, 10, 29), "sh": -2.0, "sz": 3.0},
        make_record("东方财富港股通", "complete")))
    collector = IndexDataCollector(isolate_requests=False, cache_requests=False)
    monkeypatch.setattr(collector, "_fetch_result", lambda *a, **k: CollectionResult([]))
    monkeypatch.setattr(collector, "_fetch_hk_macro", lambda: ValueResult(None, make_record("香港金管局", "failed")))
    monkeypatch.setattr(collector, "_fetch_hk_pe", lambda *a: ValueResult(None, make_record("恒生指数", "failed")))
    target = AnalysisTarget(target_type="index", symbol="UNKNOWN", name="未知", market="hk", index_style="overseas")
    ctx = collector.collect(target)
    assert ctx.capital_flow is not None and ctx.capital_flow.southbound_sh_yi == -2
    assert ctx.collection_records["index_capital_flow"].result_state == "complete"
    assert ctx.collection_records["index_sentiment"].result_state == "failed"


@pytest.mark.parametrize(("symbol", "code"), [("HSI", "00001.00"), ("HSCEI", "00014.00")])
def test_legacy_hk_symbols_fallback_to_verified_official_identity(monkeypatch, symbol, code):
    from data.base import CollectionResult
    from data.collection_state import ValueResult, make_record
    from data.schemas import AnalysisTarget
    from index.collector import IndexDataCollector
    collector = IndexDataCollector(isolate_requests=False, cache_requests=False)
    monkeypatch.setattr(collector, "_fetch_result", lambda *a, **k: CollectionResult([]))
    monkeypatch.setattr(collector, "_fetch_hk_macro", lambda: ValueResult(None, make_record("香港金管局", "failed")))
    seen = []
    def fetch_official(value):
        seen.append(value)
        return ValueResult([(date(2026, 10, 29), 100.0)], make_record("恒生官方历史", "complete"))
    monkeypatch.setattr(collector, "_fetch_hsi_official", fetch_official)
    ctx = collector.collect(AnalysisTarget(target_type="index", symbol=symbol, name=symbol, market="hk", index_style="overseas"))
    assert seen == [code]
    assert ctx.price_data[0].close == 100


def test_explicit_total_return_code_is_never_replaced_by_price_index(monkeypatch):
    from data.collection_state import ValueResult, make_record
    from data.schemas import AnalysisTarget
    from index.collector import IndexDataCollector
    collector = IndexDataCollector(isolate_requests=False, cache_requests=False)
    monkeypatch.setattr(collector, "_fetch_result", lambda *a, **k: pytest.fail("总收益版本不能请求价格指数行情"))
    monkeypatch.setattr(collector, "_fetch_hk_macro", lambda: ValueResult(None, make_record("香港金管局", "failed")))
    seen = []
    def fetch_official(value):
        seen.append(value)
        return ValueResult([(date(2026, 10, 29), 100.0)], make_record("恒生官方历史", "complete"))
    monkeypatch.setattr(collector, "_fetch_hsi_official", fetch_official)
    collector.collect(AnalysisTarget(target_type="index", symbol="HSI", name="恒指总收益", market="hk", index_style="overseas", official_index_code="10068.00"))
    assert seen == ["10068.00"]


def test_future_history_is_rejected():
    payload = {"code": "0", "data": [{"indexCode": "00001.00", "valueHistory": [
        {"date": "2099-01-01", "close": "123"},
    ]}]}
    with pytest.raises(ValueError, match="无有效收盘价"):
        parse_history(payload, "00001.00")


@pytest.mark.parametrize(("kind", "expected"), [("gross_total_return", "含税总收益"), ("net_total_return", "净总收益"), ("unknown", "收益口径未核验")])
def test_performance_never_labels_total_return_as_price(kind, expected):
    from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexClosePriceData
    from index.analysis.performance import IndexPerformanceAnalyzer
    target = AnalysisTarget(target_type="index", symbol="HSI:10068.00", name="恒指", market="hk", index_style="overseas", return_type=kind)
    ctx = IndexAnalysisContext(target=target, price_data=[IndexClosePriceData(symbol=target.symbol, trade_date=date(2026, 10, 28), close=100), IndexClosePriceData(symbol=target.symbol, trade_date=date(2026, 10, 29), close=101)])
    result = IndexPerformanceAnalyzer().analyze(ctx)
    assert expected in result.metrics["return_basis"]
    assert "价格指数" not in result.summary and "不含分红" not in result.metrics["return_basis"]



def test_official_fallback_receives_manual_refresh(monkeypatch):
    from data.base import CollectionResult
    from data.collection_state import ValueResult, make_record
    from data.schemas import AnalysisTarget
    from index.collector import IndexDataCollector

    collector = IndexDataCollector(isolate_requests=False, cache_requests=False)
    monkeypatch.setattr(collector, '_fetch_result', lambda *a, **k: CollectionResult([]))
    monkeypatch.setattr(collector, '_fetch_hk_macro', lambda **k: ValueResult(None, make_record('宏观', 'failed')))
    monkeypatch.setattr(collector, '_fetch_hk_pe', lambda *a, **k: ValueResult(None, make_record('估值', 'failed')))
    calls = []
    def official(code, *, refresh=False):
        calls.append((code, refresh))
        return ValueResult([(date(2026, 10, 29), 100)], make_record('恒生', 'complete'))
    monkeypatch.setattr(collector, '_fetch_hsi_official', official)
    target = AnalysisTarget(target_type='index', symbol='HSI:01067.00', name='恒生高股息', market='hk', index_style='overseas', official_index_code='01067.00')
    collector.collect(target, refresh_cache=True)
    assert calls == [('01067.00', True)]


def test_index_snapshot_resolves_market_and_return_type(monkeypatch):
    from typing import cast

    from data.index_mapping import IndexMappingEntry
    from data.schemas import IndexAnalysisContext
    from index.collector import IndexDataCollector
    from index.pipeline import IndexPipeline

    entry = IndexMappingEntry(symbol="HSI:10068.00", name="恒指总收益", index_style="overseas", market="hk", provider="hsi", price_symbol="10068.00", return_type="gross_total_return")
    monkeypatch.setattr("data.index_mapping.IndexMapping.resolve", lambda self, code: entry if code == "HSI:10068.00" else None)
    seen = []
    class Collector:
        def collect(self, target):
            seen.append(target)
            return IndexAnalysisContext(target=target)
    pipeline = IndexPipeline(llm_enabled=False)
    pipeline._collector = cast(IndexDataCollector, Collector())
    assert pipeline.get_snapshot("HSI:10068.00") is None
    assert (seen[0].market, seen[0].return_type, seen[0].official_index_code) == ("hk", "gross_total_return", "10068.00")
    assert pipeline.get_snapshot("UNKNOWN") is None
    assert len(seen) == 1

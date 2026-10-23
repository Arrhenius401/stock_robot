"""港股同源历史分位与 PS 当前值的离线回归。"""
from datetime import date, timedelta

import pytest

from analysis.valuation import ValuationAnalyzer
from data.akshare import AkShareAdapter
from data.enrichers.valuation_enricher import ValuationEnricher
from data.hk_eastmoney import valuation_comparison_snapshot
from data.schemas import AnalysisContext, PriceData, ValuationData


def history_context(count=130):
    start = date(2025, 10, 1)
    days = [start + timedelta(days=i) for i in range(220)]
    trading = [day for day in days if day.weekday() < 5][:count]
    valuation = ValuationData(symbol="00700.HK", date=trading[-1], pe_ttm=999, pb=999,
        pe_history={day: float(i + 1) for i, day in enumerate(days)},
        pb_history={day: float(i + 2) for i, day in enumerate(days)},
        history_source="百度港股估值（近一年）")
    prices = [PriceData(symbol="00700.HK", trade_date=day, open=10, high=10, low=10,
                        close=10, volume=10) for day in trading]
    return AnalysisContext(symbol="00700.HK", name="腾讯", market="hk",
                           valuation_data=valuation, price_data=prices)


def test_history_aligns_trading_days_and_uses_own_reference():
    ctx = ValuationEnricher().enrich(history_context())
    enriched = ctx.enriched_valuation
    assert enriched is not None
    assert len(enriched.daily_points) == 130
    assert enriched.pe_percentile == pytest.approx(round(129 / 130 * 100, 1))
    assert enriched.pe_history_summary.sample_count == 130
    assert enriched.pe_history_summary.reference != 999
    assert ctx.price_data is not None
    assert enriched.pe_history_summary.as_of == ctx.price_data[-1].trade_date


def test_each_metric_requires_120_valid_points_and_excludes_nonpositive():
    ctx = history_context()
    assert ctx.valuation_data is not None
    ctx.valuation_data.pb_history = {day: -1.0 for day in ctx.valuation_data.pb_history}
    enriched = ValuationEnricher().enrich(ctx).enriched_valuation
    assert enriched is not None
    assert enriched.pe_percentile is not None
    assert enriched.pb_percentile is None
    assert enriched.pb_history_summary.sample_count == 0
    small = ValuationEnricher().enrich(history_context(119)).enriched_valuation
    assert small is not None
    assert small.pe_percentile is None
    assert "120" in small.pe_history_summary.reason
    boundary = ValuationEnricher().enrich(history_context(120)).enriched_valuation
    assert boundary is not None
    assert boundary.pe_percentile is not None


def test_cache_json_roundtrip_preserves_history_and_provenance():
    original = history_context().valuation_data
    assert original is not None
    restored = ValuationData.model_validate_json(original.model_dump_json())
    assert restored.pe_history == original.pe_history
    assert restored.history_source == original.history_source
    legacy = ValuationData(symbol="00700.HK", date=date(2026, 1, 1), pe_ttm=10)
    assert legacy.pe_history == {}


def test_pipeline_daily_cache_retains_history_and_ps(tmp_path):
    from core.pipeline import Pipeline
    from core.registry import Registry
    from utils.config import Config

    pipeline = Pipeline(Registry(), config=Config(config_dir=tmp_path), llm_enabled=False)
    row = history_context().valuation_data
    assert row is not None
    row.ps_ttm, row.ps_as_of, row.ps_source = 3.8, date(2026, 1, 1), "东方财富港股估值对比"
    pipeline._set_cache(row.symbol, "valuation", [row])
    restored = pipeline._get_cached(row.symbol, "valuation")
    assert restored
    assert restored[0].model_dump() == row.model_dump()


def test_ps_only_cache_is_hk_only():
    from core.pipeline import Pipeline

    pipeline = Pipeline.__new__(Pipeline)
    for symbol, expected in (("00700.HK", True), ("000001", False)):
        row = ValuationData(symbol=symbol, date=date(2026, 1, 1), ps_ttm=3.8)
        assert pipeline._is_healthy("valuation", [row]) is expected


def test_collection_always_fetches_at_most_two_curves_and_retains_ps_date(mocker):
    mocker.patch("data.akshare.stock_snapshot", return_value={
        "f57": "00700", "f86": 1767225600, "f164": 10, "f167": 2})
    mocker.patch("data.akshare.valuation_comparison_snapshot", return_value={
        "REPORT_DATE": "2025-12-31 00:00:00", "PS_TTM": 3.8})
    curves = mocker.patch("data.akshare.baidu_metric", return_value={date(2025, 12, 31): 12.0})
    row = AkShareAdapter().fetch("00700.HK", market="hk", data_type="valuation")[0]
    assert curves.call_count == 2
    assert row.pe_history == {date(2025, 12, 31): 12}
    assert row.ps_ttm == 3.8
    assert row.ps_as_of == date(2025, 12, 31)
    assert row.ps_source == "东方财富港股估值对比"


def test_optional_failures_preserve_snapshot_and_explain_missing(mocker):
    mocker.patch("data.akshare.stock_snapshot", return_value={
        "f57": "00700", "f86": 1767225600, "f164": 10, "f167": 2})
    mocker.patch("data.akshare.valuation_comparison_snapshot", side_effect=TimeoutError)
    mocker.patch("data.akshare.baidu_metric", side_effect=TimeoutError)
    row = AkShareAdapter().fetch("00700.HK", market="hk", data_type="valuation")[0]
    assert row.pe_ttm == 10 and row.ps_ttm is None
    assert row.pe_history == {}
    assert any("PS" in note for note in row.valuation_notes)
    assert any("历史" in note for note in row.valuation_notes)


def test_disjoint_curves_do_not_mislabel_retained_complete_snapshot(mocker):
    mocker.patch("data.akshare.stock_snapshot", return_value={
        "f57": "00700", "f86": 1767225600, "f164": 10, "f167": 2})
    mocker.patch("data.akshare.valuation_comparison_snapshot", side_effect=TimeoutError)
    mocker.patch("data.akshare.baidu_metric", side_effect=lambda _code, metric:
        {date(2025, 12, 30 if metric == "市净率" else 31): 12.0})
    adapter = AkShareAdapter()
    row = adapter.fetch("00700.HK", market="hk", data_type="valuation")[0]
    assert (row.pe_ttm, row.pb) == (10, 2)
    diagnostic = adapter.get_collection_error()
    assert diagnostic is not None
    assert "仅保留日期较新指标" not in diagnostic.error_message


def test_ps_notes_reach_analysis_without_industry_premium():
    ctx = history_context()
    assert ctx.valuation_data is not None
    ctx.valuation_data.ps_ttm = 3.8
    ctx.valuation_data.ps_as_of = date(2026, 1, 1)
    ctx.valuation_data.ps_source = "东方财富港股估值对比"
    ValuationEnricher().enrich(ctx)
    result = ValuationAnalyzer().analyze(ctx)
    assert result.metrics["ps_ttm"] == 3.8
    assert result.metrics["target_pe_premium"] is None
    assert any("2026-01-01" in note and "PS" in note for note in result.metrics["valuation_details"])


def test_report_discloses_history_and_does_not_call_it_financial_derivation():
    from report.scoring import build_report

    ctx = ValuationEnricher().enrich(history_context())
    result = ValuationAnalyzer().analyze(ctx)
    report = build_report(ctx.symbol, ctx.name, [result], {}, ctx, no_llm=True)
    assert "近一年历史" in report and "有效样本 130" in report
    assert "参照值" in report and "百度港股估值" in report
    assert "估值序列由行情和财务派生计算" not in report


@pytest.mark.parametrize(("field", "label"), [("pe_ttm", "PE 分位"), ("pb", "PB 分位")])
def test_missing_snapshot_does_not_hide_independent_history_in_markdown(field, label):
    from report.scoring import build_report

    ctx = history_context()
    assert ctx.valuation_data is not None
    setattr(ctx.valuation_data, field, None)
    ValuationEnricher().enrich(ctx)
    result = ValuationAnalyzer().analyze(ctx)
    report = build_report(ctx.symbol, ctx.name, [result], {}, ctx, no_llm=True)
    line = next(line for line in report.splitlines() if line.startswith(f"| {label}"))
    assert "%" in line


def test_ps_request_is_bounded_and_retains_source_date(mocker):
    row = {"SECUCODE": "00700.HK", "CORRE_SECUCODE": "00700.HK",
           "REPORT_DATE": "2026-01-01 00:00:00", "PS_TTM": 3.8}
    request = mocker.patch("requests.get")
    request.return_value.json.return_value = {"success": True, "result": {"count": 1, "pages": 1, "data": [row]}}
    assert valuation_comparison_snapshot("00700") == row
    request.assert_called_once()
    assert request.call_args.kwargs["timeout"] == 10
    assert request.call_args.kwargs["params"]["pageSize"] == "1"


@pytest.mark.parametrize("alter", ["symbol", "corre", "pages", "future"])
def test_ps_request_rejects_mismatched_or_incomplete_data(mocker, alter):
    row = {"SECUCODE": "00700.HK", "CORRE_SECUCODE": "00700.HK", "REPORT_DATE": "2026-01-01 00:00:00", "PS_TTM": 3.8}
    result = {"count": 1, "pages": 1, "data": [row]}
    if alter == "symbol":
        row["SECUCODE"] = "00005.HK"
    elif alter == "corre":
        row["CORRE_SECUCODE"] = "00005.HK"
    elif alter == "pages":
        result["pages"] = 2
    else:
        row["REPORT_DATE"] = "2999-01-01 00:00:00"
    mocker.patch("requests.get").return_value.json.return_value = {"success": True, "result": result}
    with pytest.raises(ValueError):
        valuation_comparison_snapshot("00700")

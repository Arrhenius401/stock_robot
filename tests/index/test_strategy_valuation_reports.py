"""公开官方估值的报告与采集编排回归。"""
import sys
from datetime import date
from types import SimpleNamespace

import pytest

from data.schemas import (
    AnalysisTarget,
    IndexAnalysisContext,
    IndexPriceData,
    IndexValuationData,
    StrategySnapshot,
)
from index.analysis.valuation import IndexValuationAnalyzer
from index.build_single import IndexReportBuilder, render_index_report_markdown
from index.collector import IndexDataCollector


def target():
    return AnalysisTarget(target_type="index", symbol="980092", name="国证自由现金流", index_style="strategy")


def context(**kwargs):
    return IndexAnalysisContext(target=target(), valuation_data=IndexValuationData(symbol="980092", date=date(2026, 9, 30), **kwargs))


def test_snapshot_is_partial_without_claiming_ttm_or_percentile():
    ctx = context(pe_snapshot=12.2, pb=1.8, pe_basis="官方单张市盈率，未声明TTM", pb_basis="官方月末市净率", pe_as_of=date(2026,9,30), pb_as_of=date(2026,9,30), pe_source_url="https://official.example/factsheet.pdf", pb_source_url="https://official.example/factsheet.pdf", valuation_notes=["公开官方PB日频历史不可用"], valuation_valid=False)
    result = IndexValuationAnalyzer().analyze(ctx)
    assert result.status == "partial"
    assert result.metrics["pe_snapshot"] == 12.2
    assert result.metrics["pe_as_of"] == date(2026,9,30)
    assert result.metrics.get("tag") is None
    assert "TTM" not in result.summary
    assert "None" not in result.summary
    assert "分位" not in result.summary or "不判断" in result.summary
    markdown = render_index_report_markdown(IndexReportBuilder().build(ctx, [result]))
    for text in ("12.2", "1.8", "2026-09-30", "official.example", "公开官方PB日频历史不可用", "未声明TTM"):
        assert text in markdown
    assert "None%" not in markdown
    assert "分位仅供参考" not in markdown
    assert "| PE-TTM | N/A" not in markdown


def test_valid_pe_history_is_partial_because_pb_history_is_missing():
    ctx = context(pe_ttm=10, pb=1.5, pe_percentile=0, pe_sample_count=300, pb_sample_count=0, percentile_sample_start=date(2025,6,1), percentile_sample_end=date(2026,9,30), valuation_valid=True, pe_basis="官方滚动市盈率", valuation_notes=["PB日频历史不可用"])
    result = IndexValuationAnalyzer().analyze(ctx)
    assert result.status == "partial"
    assert result.metrics["tag"] == "undervalued"
    assert "300" in result.summary
    markdown = render_index_report_markdown(IndexReportBuilder().build(ctx,[result]))
    assert "0%" in markdown
    assert "2025-06-01" in markdown
    assert "实际可用样本" in markdown
    assert "样本不足" not in markdown


def test_all_values_missing_keeps_official_reason():
    result = IndexValuationAnalyzer().analyze(context(valuation_notes=["官方单张估值字段为--"], valuation_valid=False))
    assert result.status == "unavailable"
    assert "官方单张估值字段为--" in result.summary


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("etf", [False, True])
def test_strategy_collector_isolates_valuation_and_counts_progress(monkeypatch, fails, etf):
    from index.strategy_data import StrategyDataProvider
    price = IndexPriceData(symbol="980092",trade_date=date(2026,9,30),open=100,high=100,low=100,close=100,volume=0)
    monkeypatch.setattr(StrategyDataProvider,"fetch_prices", lambda *_a, **_k: [price])
    monkeypatch.setattr(StrategyDataProvider,"collect", lambda *_a, **_k: StrategySnapshot(as_of=date(2026,9,30), strategy_kind="free_cash_flow", source="官方"))
    calls=[]
    def fetch(symbol, *, provider):
        calls.append((symbol,provider))
        if fails:
            raise TimeoutError("估值超时")
        return context(pb=1.5).valuation_data
    class FakeValuationProvider(StrategyDataProvider):
        def fetch(self, symbol, *, provider):
            return fetch(symbol, provider=provider)
    monkeypatch.setitem(sys.modules, "index.valuation_data", SimpleNamespace(StrategyValuationProvider=FakeValuationProvider))
    monkeypatch.setattr(StrategyDataProvider,"fetch_etf_prices", lambda *_a, **_k: [price])
    selected=target()
    if etf:
        selected.requested_instrument={"symbol":"515300"}
    progress=[]
    ctx=IndexDataCollector().collect(selected, on_progress=lambda *args: progress.append(args))
    assert calls == [("980092","cni")]
    assert ctx.strategy_data is not None
    assert ctx.benchmark_prices
    assert [p[1] for p in progress] == list(range(1, 6 if etf else 5))
    assert all(p[2] == (5 if etf else 4) for p in progress)
    if fails:
        assert ctx.valuation_data is None
        assert any("估值" in flag for flag in ctx.risk_flags)
    else:
        assert ctx.valuation_data is not None
        assert ctx.valuation_data.pb == 1.5


def test_csi_snapshot_preserves_rolling_basis_and_separate_source():
    ctx = context(pe_ttm=9.12, pe_snapshot=8.95, pe_basis="官方日频滚动PE",
                  pe_source_url="https://official.example/daily",
                  pe_snapshot_basis="官方月末单张滚动PE（计算用股本）",
                  pe_snapshot_source_url="https://official.example/month.pdf",
                  pe_snapshot_as_of=date(2026, 8, 31), valuation_valid=False)
    result = IndexValuationAnalyzer().analyze(ctx)
    assert result.metrics["pe_snapshot_basis"] == "官方月末单张滚动PE（计算用股本）"
    assert result.metrics["pe_snapshot_source_url"].endswith("month.pdf")
    markdown = render_index_report_markdown(IndexReportBuilder().build(ctx, [result]))
    assert "官方月末单张滚动PE（计算用股本）" in markdown
    assert "https://official.example/month.pdf" in markdown
    assert "未声明TTM" not in markdown

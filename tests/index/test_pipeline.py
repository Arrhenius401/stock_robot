"""指数管道测试"""
from unittest.mock import patch

import pytest

from src.data.schemas import AnalysisTarget
from src.index.pipeline import IndexPipeline


@pytest.fixture
def mock_pipeline():
    with patch("src.index.pipeline.IndexDataCollector"), \
         patch("src.index.pipeline.IndexReportBuilder"):
        pipeline = IndexPipeline()
        yield pipeline


class TestIndexPipeline:
    def test_run_single(self, mock_pipeline):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        result = mock_pipeline.run([target])
        assert result.reports is not None
        assert result.compare is None  # 单指数不生成对比

    def test_run_multiple_generates_compare(self, mock_pipeline):
        targets = [
            AnalysisTarget(target_type="index", symbol="000300",
                           name="沪深300", market="a-shares", index_style="broad"),
            AnalysisTarget(target_type="index", symbol="000905",
                           name="中证500", market="a-shares", index_style="broad"),
        ]
        result = mock_pipeline.run(targets)
        assert result.reports is not None
        assert result.compare is not None

    def test_single_failure_does_not_block_others(self, mock_pipeline):
        targets = [
            AnalysisTarget(target_type="index", symbol="000300",
                           name="沪深300", market="a-shares", index_style="broad"),
            AnalysisTarget(target_type="index", symbol="INVALID",
                           name="无效指数", market="a-shares", index_style="broad"),
        ]
        result = mock_pipeline.run(targets)
        assert len(result.reports) <= 2

    def test_run_calls_on_progress_during_analysis(self):
        calls_record = []

        def track(stage, current, total, label):
            calls_record.append((stage, current, total, label))

        with patch("src.index.pipeline.IndexDataCollector"), \
             patch("src.index.pipeline.IndexReportBuilder"):
            pipeline = IndexPipeline()
            target = AnalysisTarget(
                target_type="index", symbol="000300",
                name="沪深300", market="a-shares", index_style="broad"
            )
            pipeline.run([target], on_progress=track)

        # broad 有 6 个分析模块
        analyze_calls = [c for c in calls_record if c[0] == "analyze"]
        assert len(analyze_calls) == 6
        for i, (_, current, total, _) in enumerate(analyze_calls):
            assert current == i + 1
            assert total == 6

    @pytest.mark.parametrize("index_style,expected_count", [
        ("broad", 6),
        ("sector", 5),
        ("overseas", 5),
    ])
    def test_analysis_modules_filtered_by_index_style(self, index_style, expected_count):
        with patch("src.index.pipeline.IndexDataCollector"), \
             patch("src.index.pipeline.IndexReportBuilder"):
            pipeline = IndexPipeline()
            target = AnalysisTarget(
                target_type="index", symbol="000300",
                name="test", market="a-shares", index_style=index_style
            )
            calls_record = []

            def track(stage, current, total, label):
                calls_record.append((stage, current, total, label))

            pipeline.run([target], on_progress=track)

        analyze_calls = [c for c in calls_record if c[0] == "analyze"]
        assert len(analyze_calls) == expected_count


def test_non_strategy_etf_request_collects_and_displays_prices(monkeypatch):
    from datetime import date

    from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexPriceData
    from index.analysis.performance import IndexPerformanceAnalyzer
    from index.build_single import IndexReportBuilder
    from index.collector import collect_etf_prices
    class Provider:
        def fetch_etf_prices(self, symbol):
            return [IndexPriceData(symbol=symbol, trade_date=date(2026, 1, d),
                    open=1, high=1.1, low=1, close=c, volume=1) for d,c in [(2,1),(3,1.1)]]
    target = AnalysisTarget(target_type="index", symbol="000300", name="沪深300", market="a-shares", index_style="broad", requested_instrument={"symbol":"510300"})
    ctx = IndexAnalysisContext(target=target, requested_instrument=target.requested_instrument,
        price_data=[*Provider().fetch_etf_prices("000300")])
    collect_etf_prices(ctx, Provider())
    assert ctx.etf_prices[-1].close == 1.1
    result = IndexPerformanceAnalyzer().analyze(ctx)
    assert result.metrics["etf_symbol"] == "510300"
    assert result.metrics["etf_return_pct"] == pytest.approx(10)
    report = IndexReportBuilder().build(ctx, [result])
    assert report.section_performance is not None
    assert report.section_performance["display"]["etf_rows"]


def test_broad_collector_invokes_etf_provider(monkeypatch):
    from unittest.mock import Mock

    from data.base import CollectionResult
    from data.schemas import AnalysisTarget
    from index.collector import IndexDataCollector
    collector = IndexDataCollector(isolate_requests=False)
    monkeypatch.setattr(collector, "_fetch_result", lambda *a, **kw: CollectionResult([]))
    provider = Mock(refresh_notes=[], fetch_etf_prices=Mock(return_value=[]))
    monkeypatch.setattr("index.strategy_data.StrategyDataProvider", lambda: provider)
    target = AnalysisTarget(target_type="index", symbol="000300", name="沪深300", market="a-shares", index_style="broad", requested_instrument={"symbol":"510300"})
    ctx = collector.collect(target, refresh_cache=True)
    provider.fetch_etf_prices.assert_called_once_with("510300")
    assert provider.refresh_cache is True
    assert any("ETF" in note for note in ctx.risk_flags)

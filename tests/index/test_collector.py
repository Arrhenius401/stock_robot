"""指数数据采集器测试"""

from datetime import date

import pytest

from data.schemas import (
    AnalysisTarget,
    CapitalFlowData,
    IndexAnalysisContext,
    IndexPriceData,
    IndexValuationData,
    MacroContext,
    NewsData,
)
from src.index.collector import IndexDataCollector


@pytest.fixture
def broad_target():
    return AnalysisTarget(
        target_type="index", symbol="000300",
        name="沪深300", market="a-shares", index_style="broad"
    )


@pytest.fixture
def sector_target():
    return AnalysisTarget(
        target_type="index", symbol="801080",
        name="电子", market="a-shares", index_style="sector"
    )


@pytest.fixture
def collector(mocker):
    """使用确定性数据源，验证采集编排而非第三方网络可用性。"""
    instance = IndexDataCollector()
    responses = {
        "index_price": [IndexPriceData(
            symbol="000300", trade_date=date(2026, 8, 27),
            open=4000, high=4050, low=3980, close=4020, volume=1_000_000,
        )],
        "index_valuation": [IndexValuationData(
            symbol="000300", date=date(2026, 8, 27), pe_ttm=14.0, pb=1.4,
        )],
        "index_capital_flow": [CapitalFlowData(
            symbol="000300", date=date(2026, 8, 27), north_bound=1.0,
        )],
        "index_macro": [MacroContext(
            symbol="000300", fetch_date=date(2026, 8, 27), pmi=50.0,
        )],
        "index_sentiment": [NewsData(
            symbol="000300", date=date(2026, 8, 27), headlines=["市场回暖"],
        )],
    }

    def fetch(_symbol, *, data_type, **_kwargs):
        return responses[data_type]

    mocker.patch.object(instance._adapter, "fetch", side_effect=fetch)
    return instance


class TestIndexDataCollector:
    def test_collect_broad_fetches_all_types(self, broad_target, collector):
        ctx = collector.collect(broad_target)
        assert isinstance(ctx, IndexAnalysisContext)
        assert ctx.target == broad_target
        # broad 应该有估值和宏观数据
        assert ctx.valuation_data is not None
        assert ctx.macro is not None

    def test_collect_sector_has_macro_none_fields(self, sector_target, collector):
        ctx = collector.collect(sector_target)
        assert ctx.macro is not None
        # sector 保留 MacroContext 实例但字段全 None
        assert ctx.macro.pmi is None
        assert ctx.macro.shibor_3m is None

    def test_collect_always_fetches_price(self, broad_target, collector):
        ctx = collector.collect(broad_target)
        assert ctx.price_data is not None

    def test_collect_calls_on_progress_for_each_step_broad(self, broad_target, collector):
        calls = []

        def track(stage, current, total, label):
            calls.append((stage, current, total, label))

        collector.collect(broad_target, on_progress=track)
        assert len(calls) == 5  # price, valuation, capital_flow, macro, sentiment
        assert all(c[0] == "collect" for c in calls)
        for i, (_, current, total, _) in enumerate(calls):
            assert current == i + 1
            assert total == 5

    def test_collect_calls_on_progress_for_sector(self, sector_target, collector):
        calls = []

        def track(stage, current, total, label):
            calls.append((stage, current, total, label))

        collector.collect(sector_target, on_progress=track)
        assert len(calls) == 4  # sector: no macro step

    def test_collect_without_on_progress_still_works(self, broad_target, collector):
        ctx = collector.collect(broad_target)  # no on_progress
        assert ctx.valuation_data is not None
        assert ctx.macro is not None

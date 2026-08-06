"""指数数据采集器测试"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import date
from src.index.collector import IndexDataCollector
from data.schemas import AnalysisTarget, IndexAnalysisContext


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


class TestIndexDataCollector:
    def test_collect_broad_fetches_all_types(self, broad_target):
        collector = IndexDataCollector()
        ctx = collector.collect(broad_target)
        assert isinstance(ctx, IndexAnalysisContext)
        assert ctx.target == broad_target
        # broad 应该有估值和宏观数据
        assert ctx.valuation_data is not None
        assert ctx.macro is not None

    def test_collect_sector_has_macro_none_fields(self, sector_target):
        collector = IndexDataCollector()
        ctx = collector.collect(sector_target)
        assert ctx.macro is not None
        # sector 保留 MacroContext 实例但字段全 None
        assert ctx.macro.pmi is None
        assert ctx.macro.shibor_3m is None

    def test_collect_always_fetches_price(self, broad_target):
        collector = IndexDataCollector()
        ctx = collector.collect(broad_target)
        assert ctx.price_data is not None

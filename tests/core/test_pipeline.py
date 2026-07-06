from unittest.mock import MagicMock
from datetime import date
from core.pipeline import Pipeline
from core.registry import Registry
from data.schemas import (
    AnalysisContext, AnalysisResult, FinancialData, PriceData,
    ValuationData, IndustryData, NewsData,
)
from data.base import DataSource


def make_test_registry():
    """构建测试用注册表"""
    class MockDataSource(DataSource):
        def supports(self, market, data_type):
            return True

        def fetch(self, symbol, **kwargs):
            data_type = kwargs.get("data_type", "price")
            if data_type == "price":
                return [PriceData(symbol=symbol, trade_date=date(2026,7,1), open=10, high=11, low=9.5, close=10.5, volume=1e6)]
            elif data_type == "financial":
                return [FinancialData(symbol=symbol, fiscal_quarter=date(2025,12,31), revenue=45e9, net_profit=8.5e9, total_assets=500e9, total_equity=45e9, operating_cash_flow=12e9)]
            elif data_type == "valuation":
                return [ValuationData(symbol=symbol, date=date(2026,7,1), pe_ttm=7.5, pb=0.85, ps_ttm=1.2)]
            elif data_type == "industry":
                return [IndustryData(symbol=symbol, industry="银行", sector="金融", peers=["600036"])]
            elif data_type == "news":
                return [NewsData(symbol=symbol, date=date(2026,7,1), headlines=["利好公告"])]
            return []

    reg = Registry()
    reg.register_data_source(MockDataSource())

    from analysis.base import AnalysisModule
    for dim in ["financial", "technical", "valuation", "industry", "sentiment"]:
        mod = MagicMock(spec=AnalysisModule)
        mod.dimension = dim
        mod.analyze.return_value = AnalysisResult(
            dimension=dim, status="ok", summary=f"{dim} analysis", metrics={}
        )
        reg.register_analysis_module(mod)
    return reg


class TestPipeline:
    def test_collect_data_populates_context(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        ctx = pipeline.collect("000001", "平安银行", "a-shares")
        assert ctx.symbol == "000001"
        assert ctx.price_data is not None
        assert ctx.financial_data is not None

    def test_run_without_llm(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        results, commentary = pipeline.run("000001", "平安银行")
        assert len(results) == 5
        assert all(isinstance(r, AnalysisResult) for r in results)

    def test_collect_refresh_cache_ignores_cache(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        ctx = pipeline.collect("000001", "平安银行", "a-shares", refresh_cache=True)
        assert ctx.price_data is not None

    def test_single_dimension_filter(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        results, _ = pipeline.run("000001", "平安银行", dimension="financial")
        assert len(results) == 1
        assert results[0].dimension == "financial"

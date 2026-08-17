from datetime import date

from analysis.financial import FinancialAnalyzer
from data.schemas import (
    AnalysisContext,
    DataSufficiency,
    DimensionSufficiency,
    FinancialData,
    SufficiencyLevel,
)


def make_ctx(**kwargs) -> AnalysisContext:
    """构造充足度标记为 SUFFICIENT 的上下文，让分析器按自身数据逻辑判定状态"""
    ctx = AnalysisContext(**kwargs)
    ctx.sufficiency = DataSufficiency(
        price=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=100, score_weight=1.0),
        financial=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=4, score_weight=1.0),
        valuation=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=100, score_weight=1.0),
        industry=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=10, score_weight=1.0),
        sentiment=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=10, score_weight=1.0),
    )
    return ctx



class TestFinancialAnalyzer:
    def test_dimension_is_financial(self):
        a = FinancialAnalyzer()
        assert a.dimension == "financial"

    def test_full_data_analysis(self):
        financials = [
            FinancialData(symbol="000001", fiscal_quarter=date(2025,12,31), revenue=45e9, net_profit=8.5e9, total_assets=500e9, total_equity=45e9, operating_cash_flow=12e9, roe=0.189, gross_margin=None),
            FinancialData(symbol="000001", fiscal_quarter=date(2025,9,30), revenue=33e9, net_profit=6.2e9, total_assets=490e9, total_equity=44e9, operating_cash_flow=9e9, roe=0.141, gross_margin=None),
            FinancialData(symbol="000001", fiscal_quarter=date(2025,6,30), revenue=22e9, net_profit=4.1e9, total_assets=485e9, total_equity=43.5e9, operating_cash_flow=6e9, roe=0.094, gross_margin=None),
            FinancialData(symbol="000001", fiscal_quarter=date(2025,3,31), revenue=11e9, net_profit=2.0e9, total_assets=475e9, total_equity=43e9, operating_cash_flow=3e9, roe=0.047, gross_margin=None),
            FinancialData(symbol="000001", fiscal_quarter=date(2024,12,31), revenue=42e9, net_profit=7.8e9, total_assets=460e9, total_equity=41e9, operating_cash_flow=11e9, roe=0.190, gross_margin=None),
        ]
        ctx = make_ctx(symbol="000001", name="平安银行", financial_data=financials)
        result = FinancialAnalyzer().analyze(ctx)
        assert result.status == "ok"
        assert "revenue_growth_yoy" in result.metrics
        assert "roe_trend" in result.metrics
        assert len(result.metrics["roe_trend"]) > 0

    def test_no_financial_data_returns_unavailable(self):
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = FinancialAnalyzer().analyze(ctx)
        assert result.status == "unavailable"

    def test_single_quarter_returns_partial(self):
        ctx = make_ctx(symbol="000001", name="测试", financial_data=[
            FinancialData(symbol="000001", fiscal_quarter=date(2025,12,31), revenue=45e9, net_profit=8.5e9, total_assets=500e9, total_equity=45e9, operating_cash_flow=12e9)
        ])
        result = FinancialAnalyzer().analyze(ctx)
        assert result.status == "partial"

    def test_analyze_tolerates_none_revenue(self):
        financials = [
            FinancialData(symbol="600350", fiscal_quarter=date(2025, 12, 31),
                          revenue=None, net_profit=8.5e8, total_assets=None,
                          total_equity=45e8, operating_cash_flow=None, roe=0.18),
            FinancialData(symbol="600350", fiscal_quarter=date(2024, 12, 31),
                          revenue=None, net_profit=7.0e8, total_assets=None,
                          total_equity=43e8, operating_cash_flow=None, roe=0.16),
        ]
        ctx = make_ctx(symbol="600350", name="山东高速", financial_data=financials)
        result = FinancialAnalyzer().analyze(ctx)
        assert result.status in ("ok", "partial")
        assert "revenue_growth_yoy" not in result.metrics
        assert "profit_growth_yoy" in result.metrics

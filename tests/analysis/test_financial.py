from datetime import date
from analysis.financial import FinancialAnalyzer
from data.schemas import AnalysisContext, FinancialData


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
        ctx = AnalysisContext(symbol="000001", name="平安银行", financial_data=financials)
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
        ctx = AnalysisContext(symbol="000001", name="测试", financial_data=[
            FinancialData(symbol="000001", fiscal_quarter=date(2025,12,31), revenue=45e9, net_profit=8.5e9, total_assets=500e9, total_equity=45e9, operating_cash_flow=12e9)
        ])
        result = FinancialAnalyzer().analyze(ctx)
        assert result.status == "partial"

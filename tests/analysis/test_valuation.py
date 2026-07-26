from datetime import date
from analysis.valuation import ValuationAnalyzer
from data.schemas import AnalysisContext, ValuationData


class TestValuationAnalyzer:
    def test_dimension_is_valuation(self):
        assert ValuationAnalyzer().dimension == "valuation"

    def test_single_valuation(self):
        ctx = AnalysisContext(symbol="000001", name="测试", valuation_data=
            ValuationData(symbol="000001", date=date(2026,7,1), pe_ttm=7.5, pb=0.85, ps_ttm=1.2)
        )
        result = ValuationAnalyzer().analyze(ctx)
        assert result.status == "partial"
        assert result.metrics["pe_ttm"] == 7.5
        assert result.metrics["pb"] == 0.85

    def test_no_data_returns_unavailable(self):
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = ValuationAnalyzer().analyze(ctx)
        assert result.status == "unavailable"

from analysis.industry import IndustryAnalyzer
from data.schemas import AnalysisContext, IndustryData


class TestIndustryAnalyzer:
    def test_dimension_is_industry(self):
        assert IndustryAnalyzer().dimension == "industry"

    def test_with_industry_data(self):
        ctx = AnalysisContext(symbol="000001", name="测试", industry_data=IndustryData(
            symbol="000001", industry="银行", sector="金融", peers=["600036", "601398"]
        ))
        result = IndustryAnalyzer().analyze(ctx)
        assert result.status == "ok"
        assert result.metrics["industry"] == "银行"
        assert len(result.metrics["peers"]) == 2

    def test_no_data_returns_unavailable(self):
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = IndustryAnalyzer().analyze(ctx)
        assert result.status == "unavailable"

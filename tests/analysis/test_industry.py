from analysis.industry import IndustryAnalyzer
from data.schemas import (
    AnalysisContext,
    DataSufficiency,
    DimensionSufficiency,
    IndustryData,
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



class TestIndustryAnalyzer:
    def test_dimension_is_industry(self):
        assert IndustryAnalyzer().dimension == "industry"

    def test_with_industry_data(self):
        ctx = make_ctx(symbol="000001", name="测试", industry_data=IndustryData(
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

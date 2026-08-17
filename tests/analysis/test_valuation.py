from datetime import date

from analysis.valuation import ValuationAnalyzer
from data.schemas import (
    AnalysisContext,
    DataSufficiency,
    DimensionSufficiency,
    SufficiencyLevel,
    ValuationData,
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



class TestValuationAnalyzer:
    def test_dimension_is_valuation(self):
        assert ValuationAnalyzer().dimension == "valuation"

    def test_single_valuation(self):
        ctx = make_ctx(symbol="000001", name="测试", valuation_data=
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

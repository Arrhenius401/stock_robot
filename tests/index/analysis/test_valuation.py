"""指数估值面分析测试"""
from datetime import datetime

from src.data.schemas import AnalysisTarget, IndexAnalysisContext, IndexValuationData
from src.index.analysis.valuation import IndexValuationAnalyzer


class TestIndexValuationAnalyzer:
    def test_dimension(self):
        assert IndexValuationAnalyzer().dimension == "index_valuation"

    def test_analyze_no_data(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        result = IndexValuationAnalyzer().analyze(ctx)
        assert result.status == "unavailable"

    def test_analyze_with_data(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        ctx.valuation_data = IndexValuationData(
            symbol="000300", date=datetime.now().astimezone().astimezone().date(),
            pe_ttm=12.5, pe_percentile=40.0,
            valuation_valid=True
        )
        result = IndexValuationAnalyzer().analyze(ctx)
        assert result.status == "ok"
        assert result.metrics["tag"] == "neutral"

    def test_analyze_invalid_valuation(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        ctx.valuation_data = IndexValuationData(
            symbol="000300", date=datetime.now().astimezone().astimezone().date(),
            pe_ttm=12.5, valuation_valid=False
        )
        result = IndexValuationAnalyzer().analyze(ctx)
        assert result.status == "partial"
        assert "分位仅供参考" in result.summary

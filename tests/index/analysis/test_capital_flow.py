"""指数资金面分析测试"""
from datetime import datetime

from src.data.schemas import (
    AnalysisTarget,
    CapitalFlowData,
    IndexAnalysisContext,
)
from src.index.analysis.capital_flow import CapitalFlowAnalyzer


class TestCapitalFlowAnalyzer:
    def test_dimension(self):
        assert CapitalFlowAnalyzer().dimension == "index_capital_flow"

    def test_analyze_no_data(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        result = CapitalFlowAnalyzer().analyze(ctx)
        assert result.status == "unavailable"

    def test_analyze_with_data(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        ctx.capital_flow = CapitalFlowData(
            symbol="000300", date=datetime.now().astimezone().astimezone().date(),
            north_bound=5.2, main_net_inflow=10.0
        )
        result = CapitalFlowAnalyzer().analyze(ctx)
        assert result.metrics["tag"] in ("positive", "neutral", "negative")

"""指数宏观面分析测试"""
from datetime import datetime

from src.data.schemas import (
    AnalysisTarget,
    IndexAnalysisContext,
    MacroContext,
)
from src.index.analysis.macro import MacroAnalyzer


class TestMacroAnalyzer:
    def test_dimension(self):
        assert MacroAnalyzer().dimension == "index_macro"

    def test_analyze_sector_na(self):
        target = AnalysisTarget(
            target_type="index", symbol="801080",
            name="电子", market="a-shares", index_style="sector"
        )
        ctx = IndexAnalysisContext(target=target)
        ctx.macro = MacroContext(symbol="801080", fetch_date=datetime.now().astimezone().astimezone().date())
        result = MacroAnalyzer().analyze(ctx)
        assert result.metrics["tag"] == "na"

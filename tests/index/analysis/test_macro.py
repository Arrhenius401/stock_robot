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


def test_overseas_macro_is_not_adapted():
    from data.schemas import AnalysisTarget, IndexAnalysisContext
    from index.analysis.macro import MacroAnalyzer
    target = AnalysisTarget(target_type="index", symbol="DAX", name="DAX", market="a-shares", index_style="overseas")
    result = MacroAnalyzer().analyze(IndexAnalysisContext(target=target))
    assert result.status == "unavailable"
    assert "尚未适配" in result.summary

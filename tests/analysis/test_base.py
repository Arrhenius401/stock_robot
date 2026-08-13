import pytest

from analysis.base import AnalysisModule
from data.schemas import AnalysisContext, AnalysisResult


class FakeModule(AnalysisModule):
    """测试用分析模块"""
    @property
    def dimension(self) -> str:
        return "financial"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        return AnalysisResult(
            dimension="financial",
            status="ok",
            summary="测试摘要",
            metrics={"revenue_growth": 0.15},
        )


class TestAnalysisModule:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            AnalysisModule()

    def test_concrete_implementation_works(self):
        mod = FakeModule()
        assert mod.dimension == "financial"
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = mod.analyze(ctx)
        assert result.status == "ok"
        assert result.dimension == "financial"

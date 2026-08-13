"""管道路由测试"""
from unittest.mock import MagicMock, patch

from src.core.pipeline import Pipeline
from src.data.schemas import AnalysisTarget


class TestPipelineRouting:
    def test_collect_accepts_analysis_target(self):
        from src.data.schemas import AnalysisContext
        pipeline = Pipeline(registry=MagicMock(), llm_enabled=False)
        target = AnalysisTarget(
            target_type="stock", symbol="000001",
            name="平安银行", market="a-shares"
        )
        with patch.object(pipeline, "collect", return_value=MagicMock(spec=AnalysisContext)):
            assert pipeline.collect_from_target(target) is not None

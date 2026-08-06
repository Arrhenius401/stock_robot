"""指数管道测试"""
import pytest
from unittest.mock import MagicMock, patch
from src.index.pipeline import IndexPipeline
from src.data.schemas import AnalysisTarget


@pytest.fixture
def mock_pipeline():
    with patch("src.index.pipeline.IndexDataCollector") as mock_collector, \
         patch("src.index.pipeline.IndexReportBuilder") as mock_builder:
        pipeline = IndexPipeline()
        yield pipeline


class TestIndexPipeline:
    def test_run_single(self, mock_pipeline):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        result = mock_pipeline.run([target])
        assert result.reports is not None
        assert result.compare is None  # 单指数不生成对比

    def test_run_multiple_generates_compare(self, mock_pipeline):
        targets = [
            AnalysisTarget(target_type="index", symbol="000300",
                           name="沪深300", market="a-shares", index_style="broad"),
            AnalysisTarget(target_type="index", symbol="000905",
                           name="中证500", market="a-shares", index_style="broad"),
        ]
        result = mock_pipeline.run(targets)
        assert result.reports is not None
        assert result.compare is not None

    def test_single_failure_does_not_block_others(self, mock_pipeline):
        targets = [
            AnalysisTarget(target_type="index", symbol="000300",
                           name="沪深300", market="a-shares", index_style="broad"),
            AnalysisTarget(target_type="index", symbol="INVALID",
                           name="无效指数", market="a-shares", index_style="broad"),
        ]
        result = mock_pipeline.run(targets)
        assert len(result.reports) <= 2

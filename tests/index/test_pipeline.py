"""指数管道测试"""
from unittest.mock import patch

import pytest

from src.data.schemas import AnalysisTarget
from src.index.pipeline import IndexPipeline


@pytest.fixture
def mock_pipeline():
    with patch("src.index.pipeline.IndexDataCollector"), \
         patch("src.index.pipeline.IndexReportBuilder"):
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

    def test_run_calls_on_progress_during_analysis(self):
        calls_record = []

        def track(stage, current, total, label):
            calls_record.append((stage, current, total, label))

        with patch("src.index.pipeline.IndexDataCollector"), \
             patch("src.index.pipeline.IndexReportBuilder"):
            pipeline = IndexPipeline()
            target = AnalysisTarget(
                target_type="index", symbol="000300",
                name="沪深300", market="a-shares", index_style="broad"
            )
            pipeline.run([target], on_progress=track)

        # broad 有 5 个分析模块
        analyze_calls = [c for c in calls_record if c[0] == "analyze"]
        assert len(analyze_calls) == 5
        for i, (_, current, total, _) in enumerate(analyze_calls):
            assert current == i + 1
            assert total == 5

    @pytest.mark.parametrize("index_style,expected_count", [
        ("broad", 5),
        ("sector", 4),
        ("overseas", 4),
    ])
    def test_analysis_modules_filtered_by_index_style(self, index_style, expected_count):
        with patch("src.index.pipeline.IndexDataCollector"), \
             patch("src.index.pipeline.IndexReportBuilder"):
            pipeline = IndexPipeline()
            target = AnalysisTarget(
                target_type="index", symbol="000300",
                name="test", market="a-shares", index_style=index_style
            )
            calls_record = []

            def track(stage, current, total, label):
                calls_record.append((stage, current, total, label))

            pipeline.run([target], on_progress=track)

        analyze_calls = [c for c in calls_record if c[0] == "analyze"]
        assert len(analyze_calls) == expected_count

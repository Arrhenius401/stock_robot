"""指数管道端到端集成测试"""
import pytest

from src.data.schemas import AnalysisTarget
from src.index.pipeline import IndexPipeline


class TestIndexPipelineIntegration:
    @pytest.mark.skip(reason="需要网络，本地开发跳过")
    def test_full_pipeline_broad(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        pipeline = IndexPipeline()
        result = pipeline.run([target])
        assert len(result.reports) == 1
        report = result.reports[0]
        assert report.code == "000300"
        assert "overview" in report.visible_sections
        assert "macro" in report.visible_sections
        assert "capital" in report.visible_sections

    @pytest.mark.skip(reason="需要网络，本地开发跳过")
    def test_full_pipeline_sector(self):
        target = AnalysisTarget(
            target_type="index", symbol="801080",
            name="电子", market="a-shares", index_style="sector"
        )
        pipeline = IndexPipeline()
        result = pipeline.run([target])
        report = result.reports[0]
        assert "macro" not in report.visible_sections

    @pytest.mark.skip(reason="需要网络，本地开发跳过")
    def test_multi_index_compare(self):
        targets = [
            AnalysisTarget(target_type="index", symbol="000300",
                           name="沪深300", market="a-shares", index_style="broad"),
            AnalysisTarget(target_type="index", symbol="000905",
                           name="中证500", market="a-shares", index_style="broad"),
        ]
        pipeline = IndexPipeline()
        result = pipeline.run(targets)
        assert len(result.reports) == 2
        assert result.compare is not None

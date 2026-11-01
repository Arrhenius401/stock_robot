"""未取得真实行情时，不生成港股指数报告。"""
from unittest.mock import Mock

from data.schemas import AnalysisTarget, IndexAnalysisContext
from index.pipeline import IndexPipeline


def test_hk_missing_price_is_an_error_without_report():
    target = AnalysisTarget(target_type="index", symbol="HSI", name="恒生指数", market="hk", index_style="overseas")
    pipeline = IndexPipeline()
    pipeline._collector = Mock()
    pipeline._collector.collect.return_value = IndexAnalysisContext(target=target)
    result = pipeline.run([target])
    assert not result.reports
    assert result.errors and "行情" in result.errors[0]

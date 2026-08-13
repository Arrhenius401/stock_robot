"""IndexContextEnricher — 个股充实器：注入所属大盘指数快照"""
import logging

from data.enricher import DataEnricher
from data.schemas import AnalysisContext

logger = logging.getLogger(__name__)


class IndexContextEnricher(DataEnricher):
    """将沪深300等基准指数快照注入个股 AnalysisContext"""

    def __init__(self, index_pipeline):
        self._index_pipeline = index_pipeline

    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        try:
            snapshot = self._index_pipeline.get_snapshot("000300")
            if snapshot:
                ctx.market_environment = snapshot
        except Exception as e:  # noqa: BLE001 — 大盘快照为辅助数据，失败不阻断
            logger.warning(f"获取大盘环境快照失败: {e}")
        return ctx

"""充实器基类和编排器"""
import logging
from abc import ABC, abstractmethod

from data.schemas import AnalysisContext

logger = logging.getLogger(__name__)


class DataEnricher(ABC):
    """充实器抽象基类"""

    @abstractmethod
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        """执行充实逻辑，返回修改后的 ctx"""
        ...


class ContextEnricher:
    """按依赖 DAG 编排所有充实器"""

    def __init__(self):
        self._enrichers: list[DataEnricher] = []

    def register(self, enricher: DataEnricher):
        self._enrichers.append(enricher)

    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        """执行全部充实器（按注册顺序，注册顺序即依赖顺序）"""
        for enricher in self._enrichers:
            try:
                ctx = enricher.enrich(ctx)
            except Exception as e:  # noqa: BLE001 — 单充实器失败不影响整体
                logger.error(f"充实器 {enricher.__class__.__name__} 失败: {e}")
        return ctx

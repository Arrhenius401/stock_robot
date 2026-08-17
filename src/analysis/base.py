from abc import ABC, abstractmethod
from typing import Any, Literal

from data.schemas import AnalysisContext, AnalysisResult

# 与 AnalysisResult.dimension 的 Literal 取值保持一致
DimensionName = Literal[
    "financial", "technical", "valuation", "industry", "sentiment",
    "index_technical", "index_valuation", "index_capital_flow",
    "index_macro", "index_sentiment",
]


class AnalysisModule(ABC):
    """分析模块抽象接口 — 所有分析器需实现此接口"""

    @property
    @abstractmethod
    def dimension(self) -> DimensionName:
        """分析维度标识"""
        ...

    @abstractmethod
    def analyze(self, context: AnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        """基于上下文执行分析，config 为行业合并后的打分配置。
        为 None 时降级为原有硬编码逻辑。"""
        ...

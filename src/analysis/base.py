from abc import ABC, abstractmethod
from src.data.schemas import AnalysisContext, AnalysisResult


class AnalysisModule(ABC):
    """分析模块抽象接口 — 所有分析器需实现此接口"""

    @property
    @abstractmethod
    def dimension(self) -> str:
        """分析维度标识"""
        ...

    @abstractmethod
    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        """基于上下文执行分析，返回统一结果"""
        ...

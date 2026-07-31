"""计分器抽象基类"""
from abc import ABC, abstractmethod
from typing import Any

from data.schemas import AnalysisContext


class BaseScorer(ABC):
    """计分器抽象基类"""

    def __init__(self, config: dict[str, Any], global_const: dict[str, Any]):
        self.config = config
        self.global = global_const

    @property
    @abstractmethod
    def dimension(self) -> str:
        """分析维度标识: financial / valuation / industry / technical / sentiment"""
        ...

    @abstractmethod
    def score(self, context: AnalysisContext) -> tuple[float, str, list[str]]:
        """返回 (得分, 得分详情文本, 风险标签列表)"""
        ...

    def _tier_score(
        self, value: float | None, tiers: list[dict],
        reverse: bool = False
    ) -> tuple[float, str]:
        """通用档位计分。tiers 按正序排列（高分在前），reverse=True 时倒序匹配。"""
        if value is None:
            return 0.0, "数据缺失"
        neg_inf = self.global.get("scoring", {}).get("negative_infinity", -999)
        ordered = list(reversed(tiers)) if reverse else tiers
        for tier in ordered:
            lo = tier.get("min", neg_inf)
            hi = tier.get("max_pct")
            if hi is None:
                hi = tier.get("max", float("inf"))
            if lo <= value <= hi:
                return tier["score"], ""
        return self.global.get("scoring", {}).get("default_score", 0), "未命中任何档位"

    def _range_score(
        self, value: float | None, tiers: list[dict]
    ) -> tuple[float, str]:
        """区间计分：value 落在 [min, max] 内则得分。"""
        if value is None:
            return 0.0, "数据缺失"
        for tier in tiers:
            lo = tier.get("min", -float("inf"))
            hi = tier.get("max", float("inf"))
            if lo <= value <= hi:
                return tier["score"], ""
        return 0, "未命中任何区间"

    def _industry_note(self) -> str:
        """返回行业特定说明文本，供 LLM prompt 使用。由子类覆写。"""
        return ""

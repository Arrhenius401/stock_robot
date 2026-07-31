"""计分器抽象基类"""
from abc import ABC, abstractmethod
from typing import Any

from data.schemas import AnalysisContext


class BaseScorer(ABC):
    """计分器抽象基类"""

    def __init__(self, config: dict[str, Any], global_const: dict[str, Any]):
        self.config = config
        self.global_const = global_const

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
        """通用档位计分。

        正向模式（reverse=False）：tiers 按正序排列（高分在前），
        max_pct 为百分位上限，value <= max_pct 时命中。

        反转模式（reverse=True）：max_pct 作为百分位下限（>=逻辑），
        高分位得高分，用于周期资源等需要 PE 反转解读的场景。
        """
        if value is None:
            return 0.0, "数据缺失"
        neg_inf = self.global_const.get("scoring", {}).get("negative_infinity", -999)
        for tier in tiers:
            if reverse:
                # 反转模式：max_pct 作为最小阈值（高百分位 → 高分）
                lo = tier.get("max_pct")
                if lo is not None:
                    if value >= lo:
                        return tier["score"], ""
                else:
                    # 无阈值兜底 tier
                    return tier["score"], ""
            else:
                # 正向模式：max_pct 作为上限
                lo = tier.get("min", neg_inf)
                hi = tier.get("max_pct")
                if hi is None:
                    hi = tier.get("max", float("inf"))
                if lo <= value <= hi:
                    return tier["score"], ""
        return self.global_const.get("scoring", {}).get("default_score", 0), "未命中任何档位"

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

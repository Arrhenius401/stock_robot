"""周期资源计分器 — PE 反转逻辑 + 毛利率波动豁免"""
from analysis.scorers.general import GeneralScorer
from data.schemas import AnalysisContext


class CyclicalScorer(GeneralScorer):
    """继承 GeneralScorer，PE 分位反转解读，毛利率波动不计分。"""

    def _industry_note(self) -> str:
        return ("周期资源行业采用 PE 反转估值逻辑：高 PE 分位对应周期底部（低盈利阶段），"
                "低 PE 分位对应周期顶部（高盈利阶段），与一般行业的 PE 解读相反。"
                "毛利率波动不参与打分，周期性波动视为常态。")

    def score_valuation(self, ctx: AnalysisContext) -> tuple[float | None, str, list[str]]:
        return self._score_valuation(ctx, cyclical=True)

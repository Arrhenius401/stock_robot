"""银行专用计分器 — PB 为主要估值指标，自定义风控指标"""
from analysis.scorers.general import GeneralScorer
from data.schemas import AnalysisContext


class BankScorer(GeneralScorer):
    """继承 GeneralScorer，覆写财务和估值维度的计分逻辑。"""

    def _industry_note(self) -> str:
        return ("银行业采用 PB 估值为主，PE 分位不适用。"
                "财务维度重点关注不良贷款率、拨备覆盖率、资本充足率，"
                "不采用通用毛利率和资产负债率判定。")

    def score_valuation(self, ctx: AnalysisContext) -> tuple[float | None, str, list[str]]:
        return self._score_valuation(ctx, pe_applicable=False)

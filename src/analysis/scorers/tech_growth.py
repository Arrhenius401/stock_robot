"""科技成长计分器 — 研发率、营收增速替代 ROE"""
from analysis.scorers.general import GeneralScorer


class TechGrowthScorer(GeneralScorer):
    def _industry_note(self) -> str:
        return ("TMT科技行业对 ROE 容忍度较高，估值侧重营收增速和研发投入。"
                "轻资产加分，负债率过低不扣分。")

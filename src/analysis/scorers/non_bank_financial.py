"""非银金融计分器 — 券商/保险差异化"""
from analysis.scorers.general import GeneralScorer


class NonBankFinancialScorer(GeneralScorer):
    def _industry_note(self) -> str:
        return ("非银金融行业（券商/保险）估值侧重 PB+ROE，"
                "毛利率不适用。券商关注代理买卖收入，保险关注内含价值（PEV）。")

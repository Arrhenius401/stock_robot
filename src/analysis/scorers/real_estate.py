"""房地产计分器 — 有息负债口径"""
from analysis.scorers.general import GeneralScorer


class RealEstateScorer(GeneralScorer):
    def _industry_note(self) -> str:
        return ("房地产行业采用有息负债口径评估偿债风险，"
                "不直接使用通用总资产负债率一刀切评判。")

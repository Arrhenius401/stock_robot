"""医药计分器 — 研发管线估值"""
from analysis.scorers.general import GeneralScorer


class PharmaScorer(GeneralScorer):
    def _industry_note(self) -> str:
        return ("医药行业高研发投入特征，短期 ROE 容忍度较高。"
                "创新药和仿制药采用不同估值逻辑，关注研发管线进度。")

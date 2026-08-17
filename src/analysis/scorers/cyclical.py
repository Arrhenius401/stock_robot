"""周期资源计分器 — PE 反转逻辑 + 毛利率波动豁免"""
from analysis.scorers.general import GeneralScorer
from data.schemas import AnalysisContext


class CyclicalScorer(GeneralScorer):
    """继承 GeneralScorer，PE 分位反转解读，毛利率波动不计分。"""

    def _industry_note(self) -> str:
        return ("周期资源行业采用 PE 反转估值逻辑：高 PE 分位对应周期底部（低盈利阶段），"
                "低 PE 分位对应周期顶部（高盈利阶段），与一般行业的 PE 解读相反。"
                "毛利率波动不参与打分，周期性波动视为常态。")

    def score_valuation(self, ctx: AnalysisContext) -> tuple[float, str, list[str]]:
        cfg = self.config.get("valuation", {})
        ev = ctx.enriched_valuation
        ei = ctx.enriched_industry

        total = 0.0
        details = []
        risks = []

        # PE — 反转逻辑
        pe_cfg = cfg.get("pe_percentile", {})
        if pe_cfg.get("enabled", True):
            pe_pct = ev.pe_percentile if ev else None
            reverse = pe_cfg.get("percentile_reverse", False)
            s, _ = self._tier_score(pe_pct, pe_cfg.get("tiers", []), reverse=reverse)
            max_s = pe_cfg.get("max_score", 4)
            total += s
            pct_label = f"{pe_pct:.0f}%" if pe_pct is not None else "缺失"
            details.append(f"PE 分位 {pct_label}（周期反转解读），得 {s}/{max_s} 分")

        # PB — 通用
        pb_cfg = cfg.get("pb_percentile", {})
        if pb_cfg.get("enabled", True):
            pb_pct = ev.pb_percentile if ev else None
            pb_val = ctx.valuation_data.pb if ctx.valuation_data else None
            if pb_val is not None and pb_val <= 0:
                details.append("PB 为负，得 0 分")
            elif pb_pct is not None:
                s, _ = self._tier_score(pb_pct, pb_cfg.get("tiers", []))
                max_s = pb_cfg.get("max_score", 2)
                total += s
                details.append(f"PB 分位 {pb_pct:.0f}%，得 {s}/{max_s} 分")

        # 行业溢价
        prem_cfg = cfg.get("industry_premium", {})
        if prem_cfg.get("enabled", True) and ei and ei.target_pe_premium is not None:
            s, _ = self._tier_score(ei.target_pe_premium, prem_cfg.get("tiers", []))
            max_s = prem_cfg.get("max_score", 4)
            total += s
            details.append(f"行业溢价 {ei.target_pe_premium:.0f}%，得 {s}/{max_s} 分")

        return round(total, 1), "；".join(details), risks

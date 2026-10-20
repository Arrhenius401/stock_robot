"""周期资源计分器 — PE 反转逻辑 + 毛利率波动豁免"""
from analysis.scorers.general import GeneralScorer
from analysis.valuation_rules import valuation_evidence
from data.schemas import AnalysisContext


class CyclicalScorer(GeneralScorer):
    """继承 GeneralScorer，PE 分位反转解读，毛利率波动不计分。"""

    def _industry_note(self) -> str:
        return ("周期资源行业采用 PE 反转估值逻辑：高 PE 分位对应周期底部（低盈利阶段），"
                "低 PE 分位对应周期顶部（高盈利阶段），与一般行业的 PE 解读相反。"
                "毛利率波动不参与打分，周期性波动视为常态。")

    def score_valuation(self, ctx: AnalysisContext) -> tuple[float, str, list[str]]:
        cfg = self.config.get("valuation", {})

        total = 0.0
        details = []
        risks = []

        # PE — 反转逻辑
        pe_cfg = cfg.get("pe_percentile", {})
        if pe_cfg.get("enabled", True):
            pe_pct, _, _ = valuation_evidence(ctx)
            reverse = pe_cfg.get("percentile_reverse", False)
            s, _ = self._tier_score(pe_pct, pe_cfg.get("tiers", []), reverse=reverse)
            max_s = pe_cfg.get("max_score", 4)
            total += s
            pct_label = f"{pe_pct:.2f}%" if pe_pct is not None else "不适用或历史样本不足"
            details.append(f"PE 分位 {pct_label}（周期反转解读），得 {s}/{max_s} 分")

        # PB — 通用
        pb_cfg = cfg.get("pb_percentile", {})
        if pb_cfg.get("enabled", True):
            _, pb_pct, _ = valuation_evidence(ctx)
            if pb_pct is None:
                details.append("PB 分位不适用或历史样本不足，不计分")
            else:
                s, _ = self._tier_score(pb_pct, pb_cfg.get("tiers", []))
                max_s = pb_cfg.get("max_score", 2)
                total += s
                details.append(f"PB 分位 {pb_pct:.2f}%，得 {s}/{max_s} 分")

        # 行业溢价
        prem_cfg = cfg.get("industry_premium", {})
        _, _, premium = valuation_evidence(ctx)
        if prem_cfg.get("enabled", True) and premium is not None:
            s, _ = self._tier_score(premium, prem_cfg.get("tiers", []))
            max_s = prem_cfg.get("max_score", 4)
            total += s
            details.append(f"行业溢价 {premium:.2f}%，得 {s}/{max_s} 分")

        return round(total, 1), "；".join(details), risks

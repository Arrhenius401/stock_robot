"""估值分析模块 — PE/PB/PS 当前值与历史分位"""
from analysis.base import AnalysisModule
from data.schemas import AnalysisContext, AnalysisResult, ValuationData


class ValuationAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "valuation"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        val_data = context.valuation_data
        if val_data is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="估值数据不可用", metrics={})

        latest = val_data
        metrics = {
            "pe_ttm": latest.pe_ttm,
            "pb": latest.pb,
            "ps_ttm": latest.ps_ttm,
        }

        # 若有充实层估值数据，优先使用日频序列计算分位
        if context.enriched_valuation and context.enriched_valuation.pe_percentile is not None:
            metrics["pe_percentile"] = context.enriched_valuation.pe_percentile

        status = "partial"
        summary = self._build_summary(metrics)

        # ===== 打分逻辑 =====
        from data.schemas import SufficiencyLevel

        if context.sufficiency and context.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="估值数据不足", metrics=metrics,
                                  score=None, score_detail="估值维度数据不足，跳过打分")

        score = 0.0
        score_parts = []
        risk_flags = []
        ev = context.enriched_valuation
        ei = context.enriched_industry

        # 1. PE 历史分位 (4分)
        pe_pct = ev.pe_percentile if ev else None
        if pe_pct is not None:
            if pe_pct < 30:
                score += 4; score_parts.append(f"PE 处于 {pe_pct:.0f}% 分位（低估区间），得 4/4 分")
            elif pe_pct < 50:
                score += 3; score_parts.append(f"PE 处于 {pe_pct:.0f}% 分位，得 3/4 分")
            elif pe_pct < 70:
                score += 2; score_parts.append(f"PE 处于 {pe_pct:.0f}% 分位（中性区间），得 2/4 分")
            elif pe_pct < 90:
                score += 1; score_parts.append(f"PE 处于 {pe_pct:.0f}% 分位（偏高），得 1/4 分")
                risk_flags.append("high_pe_premium")
            else:
                score += 0; score_parts.append(f"PE 处于 {pe_pct:.0f}% 分位（极高），得 0/4 分")
                risk_flags.append("high_pe_premium")
        else:
            score_parts.append("PE 历史分位数据缺失，得 0/4 分")

        # 2. PB 历史分位 (2分)
        pb_pct = ev.pb_percentile if ev else None
        pb_val = metrics.get("pb")
        if pb_val is not None and pb_val <= 0:
            score += 0; score_parts.append("PB 为负，得 0/2 分")
        elif pb_pct is not None:
            if pb_pct < 30:
                score += 2; score_parts.append(f"PB 处于 {pb_pct:.0f}% 分位，得 2/2 分")
            elif pb_pct < 70:
                score += 1; score_parts.append(f"PB 处于 {pb_pct:.0f}% 分位，得 1/2 分")
            else:
                score += 0; score_parts.append(f"PB 处于 {pb_pct:.0f}% 分位（偏高），得 0/2 分")
        else:
            score_parts.append("PB 历史分位数据缺失，得 0/2 分")

        # 3. 行业溢价程度 (4分)
        premium = ei.target_pe_premium if ei else None
        if premium is None and pe_pct is None:
            score_parts.append("行业溢价数据缺失，得 0/4 分")
            risk_flags.append("high_pe_premium")
        elif premium is not None:
            if premium < -20:
                score += 4; score_parts.append(f"相对行业 PE 折价 {abs(premium):.0f}%（>20%），得 4/4 分")
            elif premium < 0:
                score += 3; score_parts.append(f"相对行业 PE 折价 {abs(premium):.0f}%，得 3/4 分")
            elif premium < 20:
                score += 2; score_parts.append(f"相对行业 PE 溢价 {premium:.0f}%，得 2/4 分")
            elif premium < 50:
                score += 1; score_parts.append(f"相对行业 PE 溢价 {premium:.0f}%，得 1/4 分")
            else:
                score += 0; score_parts.append(f"相对行业 PE 溢价 {premium:.0f}%（>50%），得 0/4 分")
        else:
            score_parts.append("行业对比数据缺失，得 0/4 分")

        score = round(score, 1)
        score_detail = "；".join(score_parts)
        partial_status = "partial" if (context.sufficiency and context.sufficiency.valuation.level == SufficiencyLevel.PARTIAL) else status
        return AnalysisResult(dimension=self.dimension, status=partial_status, summary=summary,
                              metrics=metrics, score=score, score_detail=score_detail,
                              risk_flags=risk_flags)

    def _build_summary(self, metrics: dict) -> str:
        parts = []
        pe = metrics.get("pe_ttm")
        if pe is not None:
            parts.append(f"PE(TTM) {pe:.2f}")
        pb = metrics.get("pb")
        if pb is not None:
            parts.append(f"PB {pb:.2f}")
        pct = metrics.get("pe_percentile")
        if pct is not None:
            parts.append(f"PE处于历史{pct}%分位")
        return "；".join(parts) if parts else "估值数据不足"

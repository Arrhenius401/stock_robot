"""银行专用计分器 — PB 为主要估值指标，自定义风控指标"""
from data.schemas import AnalysisContext
from analysis.scorers.general import GeneralScorer


class BankScorer(GeneralScorer):
    """继承 GeneralScorer，覆写财务和估值维度的计分逻辑。"""

    def _industry_note(self) -> str:
        return ("银行业采用 PB 估值为主（权重 35%），PE 不适用。"
                "财务维度重点关注不良贷款率、拨备覆盖率、资本充足率，"
                "不采用通用毛利率和资产负债率判定。")

    def score_financial(self, ctx: AnalysisContext) -> tuple[float, str, list[str]]:
        cfg = self.config.get("financial", {})
        financials = ctx.financial_data or []
        if not financials:
            return 0.0, "财务数据不可用", []
        sorted_data = sorted(financials, key=lambda x: x.fiscal_quarter, reverse=True)
        latest = sorted_data[0]

        total = 0.0
        details = []
        risks = []

        # ROE — 走 YAML 档位（已在 override/银行.yaml 中定制）
        roe_cfg = cfg.get("roe", {})
        if roe_cfg.get("enabled", True):
            s, _ = self._tier_score(latest.roe, roe_cfg.get("tiers", []))
            max_s = roe_cfg.get("max_score", 3)
            total += s
            label = f"{latest.roe*100:.1f}%" if latest.roe is not None else "缺失"
            details.append(f"ROE {label}，得 {s}/{max_s} 分")
            if latest.roe is not None and latest.roe < 0:
                risks.append("roe_low")

        # 银行专项风控 — use_custom_calc 接管
        debt_cfg = cfg.get("debt_ratio", {})
        if debt_cfg.get("use_custom_calc"):
            details.append("银行专项风控：数据接口待接入（不良率/拨备覆盖率/资本充足率）")

        # 现金流匹配 — 通用
        cf_cfg = cfg.get("cashflow_match", {})
        if cf_cfg.get("enabled", True):
            np_val = latest.net_profit
            ocf = latest.operating_cash_flow
            if np_val is not None and np_val <= 0:
                details.append("净利润为负，现金流匹配 0 分")
                risks.append("cash_flow_mismatch")
            elif ocf is not None and np_val is not None and np_val > 0:
                ratio = ocf / np_val
                s, _ = self._tier_score(ratio, cf_cfg.get("tiers", []))
                max_s = cf_cfg.get("max_score", 3)
                total += s
                details.append(f"经营现金流/净利润 {ratio:.2f}，得 {s}/{max_s} 分")

        # 毛利率 — 银行已 disabled
        gm_cfg = cfg.get("gross_margin_stability", {})
        if gm_cfg.get("enabled", True):
            details.append("毛利率不适用于银行业，已跳过")

        return round(total, 1), "；".join(details), risks

    def score_valuation(self, ctx: AnalysisContext) -> tuple[float, str, list[str]]:
        cfg = self.config.get("valuation", {})
        ev = ctx.enriched_valuation
        ei = ctx.enriched_industry

        total = 0.0
        details = []
        risks = []

        # PE — 银行已 disabled
        pe_cfg = cfg.get("pe_percentile", {})
        if pe_cfg.get("enabled", True):
            details.append("PE 不适用于银行业，已跳过")

        # PB — 核心估值指标，权重提升至 6 分
        pb_cfg = cfg.get("pb_percentile", {})
        if pb_cfg.get("enabled", True):
            pb_pct = ev.pb_percentile if ev else None
            pb_val = ctx.valuation_data.pb if ctx.valuation_data else None
            if pb_val is not None and pb_val <= 0:
                details.append("PB 为负，得 0 分")
            elif pb_pct is not None:
                s, _ = self._tier_score(pb_pct, pb_cfg.get("tiers", []))
                max_s = pb_cfg.get("max_score", 6)
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

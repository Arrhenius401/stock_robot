"""银行专用计分器 — PB 为主要估值指标，自定义风控指标"""
from analysis.scorers.general import GeneralScorer
from data.schemas import AnalysisContext


class BankScorer(GeneralScorer):
    """继承 GeneralScorer，覆写财务和估值维度的计分逻辑。"""

    def _industry_note(self) -> str:
        return ("银行业采用 PB 估值为主，PE 分位不适用。"
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
            label = f"{latest.roe*100:.2f}%" if latest.roe is not None else "缺失"
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

    def score_valuation(self, ctx: AnalysisContext) -> tuple[float | None, str, list[str]]:
        return self._score_valuation(ctx, pe_applicable=False)

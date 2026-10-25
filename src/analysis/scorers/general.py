"""通用计分器 — 纯 YAML 驱动，无硬编码阈值"""
from analysis.financial_scoring import (
    cashflow_compatible,
    debt_ratio,
    gross_margin_periods,
    trusted_rows,
)
from analysis.scorers.base import BaseScorer
from analysis.technical_indicators import calculate_macd, moving_average, volume_ratio
from analysis.valuation_rules import valuation_evidence
from data.financial_scoring_facts import annual_roe_fact
from data.schemas import AnalysisContext
from utils.numbers import is_finite_number, is_positive_finite


class GeneralScorer(BaseScorer):
    """通用计分器 — 为每个维度提供独立的计分方法，全部参数从 YAML 读取。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._method_map = {
            "financial": self.score_financial,
            "valuation": self.score_valuation,
            "industry": self.score_industry,
            "technical": self.score_technical,
            "sentiment": self.score_sentiment,
        }

    @property
    def dimension(self) -> str:
        return "general"

    def score(self, context: AnalysisContext) -> tuple[float, str, list[str]]:
        raise NotImplementedError("使用 score_dimension(dim, context) 按维度计分")

    def score_dimension(
        self, dim: str, context: AnalysisContext
    ) -> tuple[float | None, str, list[str]]:
        method = self._method_map.get(dim)
        if method is None:
            return 0.0, f"未知维度: {dim}", []
        return method(context)

    # —— 财务维度 ——
    def score_financial(self, ctx: AnalysisContext) -> tuple[float | None, str, list[str]]:
        cfg = self.config.get("financial", {})
        self.financial_score_basis = {"available_max": 0.0, "eligible_max": 0.0, "reasons": []}
        if not cfg.get("enabled", True):
            return None, "财务维度已禁用", []
        rows = trusted_rows(ctx)
        if not rows:
            return None, "财务数据不可用或期间冲突", []
        latest = rows[0]
        total, available, eligible = 0.0, 0.0, 0.0
        details: list[str] = []
        risks: list[str] = []
        reasons: list[str] = []
        fact = annual_roe_fact(ctx)
        gms = gross_margin_periods(rows)
        financial_sector = self.config.get("meta", {}).get("strategy_key") in ("BankScorer", "NonBankFinancialScorer")
        for key, label, default_max in (("roe", "年度 ROE", 3), ("debt_ratio", "资产负债率", 2),
                                        ("cashflow_match", "经营现金流/净利润", 3),
                                        ("gross_margin_stability", "毛利率稳定性", 2)):
            item = cfg.get(key, {})
            if not item.get("enabled", True):
                continue
            if financial_sector and key in ("debt_ratio", "gross_margin_stability"):
                details.append(f"{label}通用口径不适用于银行/保险等金融业，已跳过；专项数据待接入")
                continue
            maximum = item.get("max_score", default_max)
            if not is_positive_finite(maximum):
                reasons.append(f"{label}最大分无效，已跳过")
                continue
            eligible += maximum
            value, reason, score = None, "", 0.0
            if item.get("use_custom_calc"):
                reason = "专项指标接口未接入"
            elif key == "roe":
                value = fact["value"]
                reason = fact.get("reason", "")
                if value is not None:
                    score, _ = self._tier_score(value, item.get("tiers", []))
                    label += (f" {value*100:.2f}%（{fact['period_start']} 至 {fact['period_end']}；"
                              f"{fact['source']}；{fact['basis']}）")
                    if value < 0 and item.get("extra_config", {}).get("negative_to_zero"):
                        risks.append("roe_low")
            elif key == "debt_ratio":
                value = debt_ratio(latest)
                if value is not None:
                    score, _ = self._range_score(value, item.get("tiers", []))
                    label += f" {value:.2f}%（{latest.fiscal_quarter}）"
                    if value > item.get("extra_config", {}).get("extreme_threshold", 90):
                        risks.append("high_debt")
                else:
                    reason = "最新资产/权益来源或同口径证据不足"
            elif key == "cashflow_match":
                if cashflow_compatible(latest):
                    profit, cash = latest.net_profit, latest.operating_cash_flow
                    if profit is not None and cash is not None and profit > 0:
                        value = cash / profit
                        if not is_finite_number(value):
                            value = None
                        else:
                            score, _ = self._tier_score(value, item.get("tiers", []))
                            label += f" {value:.2f}（{latest.period_start} 至 {latest.fiscal_quarter}，{latest.report_currency}，{latest.cash_flow_basis}）"
                            if value < 0.5:
                                risks.append("cash_flow_mismatch")
                    elif item.get("extra_config", {}).get("negative_profit_zero"):
                        value = 0.0
                        label += " 净利润非正"
                        risks.append("cash_flow_mismatch")
                    else:
                        reason = "净利润非正，比例不适用"
                else:
                    reason = "现金流与利润缺少同起止期、同币种或来源证据"
            elif len(gms) >= 2:
                value = (max(gms) - min(gms)) * 100
                if not is_finite_number(value):
                    value = None
                else:
                    if not (any(g < 0 for g in gms) and item.get("extra_config", {}).get("negative_gm_zero")):
                        for tier in item.get("tiers", []):
                            if value <= tier.get("max_vol_pp", float("inf")):
                                score = tier["score"]
                                break
                    label += f" 最近{len(gms)}期同类型同口径波动 {value:.2f}pp"
            else:
                reason = "同类型同口径毛利率不足两期"
            if value is None:
                missing = f"{label}：{reason or '数据无效'}，不计分"
                details.append(missing)
                reasons.append(missing)
                continue
            total += score
            available += maximum
            details.append(f"{label}，得 {score}/{maximum} 分")
        self.financial_score_basis = {"available_max": available, "eligible_max": eligible, "reasons": reasons}
        if not available:
            return None, "；".join([*details, "没有可用财务计分子项，不计分"]), risks
        normalized = total / available * 10
        details.append(f"有效子项 {total:.2f}/{available:.2f} 分，按原相对权重折算为 {normalized:.1f}/10")
        return normalized, "；".join(details), risks

    # —— 估值维度 ——
    def score_valuation(self, ctx: AnalysisContext) -> tuple[float | None, str, list[str]]:
        return self._score_valuation(ctx)

    def _score_valuation(
        self, ctx: AnalysisContext, *, pe_applicable: bool = True, cyclical: bool = False,
    ) -> tuple[float | None, str, list[str]]:
        """只对有效且启用的估值子项按原配置相对权重折算。"""
        cfg = self.config.get("valuation", {})
        if not cfg.get("enabled", True):
            return None, "估值维度不计分", []
        pe_pct, pb_pct, premium = valuation_evidence(ctx)
        total = 0.0
        available_max = 0.0
        details: list[str] = []
        risks: list[str] = []
        items = (
            ("pe_percentile", "PE 分位", pe_pct, 4),
            ("pb_percentile", "PB 分位", pb_pct, 2),
            ("industry_premium", "同行 PE 溢价", premium, 4),
        )
        for key, label, value, default_max in items:
            item = cfg.get(key, {})
            if not item.get("enabled", True):
                continue
            if key in ("pe_percentile", "industry_premium") and not pe_applicable:
                details.append("PE 不适用于银行业，已跳过，不计分")
                continue
            maximum = item.get("max_score", default_max)
            if not is_positive_finite(maximum):
                details.append(f"{label}权重无效，不计分")
                continue
            if value is None:
                details.append(f"{label}不适用或数据不足，不计分")
                continue
            reverse = key == "pe_percentile" and item.get("percentile_reverse", False)
            score, _ = self._tier_score(value, item.get("tiers", []), reverse=reverse)
            total += score
            available_max += maximum
            suffix = "（周期反转解读）" if key == "pe_percentile" and cyclical else ""
            details.append(f"{label} {value:.2f}%{suffix}，得 {score}/{maximum} 分")
            if key == "pe_percentile" and value > 90 and not cyclical:
                risks.append("high_pe_premium")
        if not available_max:
            details.append("没有可用估值计分子项，不计分")
            return None, "；".join(details), []
        normalized = total / available_max * 10
        details.append(f"有效子项 {total:.2f}/{available_max:.2f} 分，按原相对权重折算为 {normalized:.1f}/10")
        return normalized, "；".join(details), risks

    # —— 行业维度 ——
    def score_industry(self, ctx: AnalysisContext) -> tuple[float | None, str, list[str]]:
        from analysis.industry_scoring import score_industry_comparison

        comparison = ctx.enriched_industry.comparison if ctx.enriched_industry else {}
        score, detail, _ = score_industry_comparison(comparison)
        return score, detail, []

    # —— 技术维度 ——
    def score_technical(self, ctx: AnalysisContext) -> tuple[float | None, str, list[str]]:
        cfg = self.config.get("technical", {})
        if not cfg.get("enabled", True):
            return None, "技术维度已禁用", []
        prices = sorted(ctx.price_data or [], key=lambda x: x.trade_date)
        if not prices:
            return None, "行情数据不可用", []
        closes = [p.close for p in prices]
        total, available = 0.0, 0.0
        details: list[str] = []
        risks: list[str] = []

        def add(key: str, label: str, condition: str | None, fallback: float, default_max: float):
            nonlocal total, available
            item = cfg.get(key, {})
            if not item.get("enabled", True):
                return
            maximum = item.get("max_score", default_max)
            if condition is None or not is_positive_finite(maximum):
                details.append(f"{label}数据不足或权重无效，不计分")
                return
            score = next((t["score"] for t in item.get("tiers", []) if t.get("condition") == condition), fallback)
            total += score
            available += maximum
            details.append(f"{label}，得 {score}/{maximum} 分")

        ma5, ma20, ma60 = (moving_average(closes, n) for n in (5, 20, 60))
        if ma5 and ma20 and ma60:
            condition = "bullish" if ma5 > ma20 > ma60 else "cross" if ma5 > ma20 and ma20 < ma60 else "bearish"
            label, fallback = {"bullish": ("多头排列", 4), "cross": ("均线交叉震荡", 2), "bearish": ("空头排列", 1)}[condition]
            if condition == "bearish" and cfg.get("ma_structure", {}).get("enabled", True):
                risks.append("bearish_ma")
            add("ma_structure", label, condition, fallback, 4)
        elif ma5 and ma20:
            add("ma_structure", "缺 MA60", "cross", 2, 4)
        else:
            add("ma_structure", "均线结构", None, 0, 4)
        ratio = volume_ratio([p.volume for p in prices], require_complete_window=False)
        if len(closes) >= 20 and ma20 and ma20 > 0 and ratio is not None:
            distance = (closes[-1] - ma20) / ma20 * 100
            condition = ("up_with_volume" if distance > 0 and ratio > 1.2 else
                         "sideways_shrink" if abs(distance) < 2 and .8 <= ratio <= 1.2 else
                         "down_with_volume" if distance < 0 and ratio > 1.2 else "normal")
            label, fallback = {"up_with_volume": ("放量上涨", 3), "sideways_shrink": ("横盘缩量", 2),
                               "down_with_volume": ("放量下跌", 1), "normal": ("量价一般", 2)}[condition]
            add("volume_price", f"{label}（量比 {ratio:.2f}）", condition, fallback, 3)
            if condition == "down_with_volume" and cfg.get("volume_price", {}).get("enabled", True):
                risks.append("volume_bearish")
        else:
            add("volume_price", "量价配合", None, 0, 3)
        if len(closes) >= 34:
            macd = calculate_macd(closes)
            condition = "bullish" if macd.dif > macd.dea and macd.bar > 0 else "critical" if (macd.dif > macd.dea) != (macd.bar > 0) else "bearish"
            label, fallback = {"bullish": ("MACD 多头", 3), "critical": ("MACD 临界", 2), "bearish": ("MACD 空头", 1)}[condition]
            add("macd", label, condition, fallback, 3)
        else:
            add("macd", "MACD", None, 0, 3)
        if not available:
            return None, "；".join([*details, "没有可用技术计分子项，不计分"]), risks
        normalized = total / available * 10
        details.append(f"有效子项 {total:.2f}/{available:.2f} 分，按原相对权重折算为 {normalized:.1f}/10")
        return normalized, "；".join(details), risks

    # —— 舆情维度 ——
    def score_sentiment(self, ctx: AnalysisContext) -> tuple[float | None, str, list[str]]:
        from analysis.sentiment_rules import score_sentiment

        return score_sentiment(ctx)

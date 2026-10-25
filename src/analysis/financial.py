"""财务分析模块 — 配置驱动，无硬编码阈值"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from analysis.financial_trends import build_financial_trends
from data.schemas import AnalysisContext, AnalysisResult, SufficiencyLevel
from utils.numbers import is_finite_number


class FinancialAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["financial"]:
        return "financial"

    def analyze(self, context: AnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        meta = config.get("meta", {}) if config else {}
        template_note = ""
        template_metrics: dict[str, Any] = {}
        if context.market == "hk" and meta.get("scoring_template_source"):
            fallback = "是，使用通用规则" if meta.get("scoring_template_fallback") else "否，按真实 HSICS 匹配投资风格"
            template_note = (f"评分模板：{meta['scoring_template_source']}；投资风格：{meta.get('style_category', '未记录')}；"
                             f"通用规则回退：{fallback}；真实 HSICS 分类不变")
            template_metrics = {"评分模板": template_note}
        financials = context.financial_data or []
        if not financials:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="财务数据不可用", metrics=template_metrics, score_detail=template_note)

        sorted_data = sorted(financials, key=lambda x: x.fiscal_quarter, reverse=True)
        latest = sorted_data[0]

        if context.market == "hk":
            amount_basis = (f"金额币种：{latest.report_currency}（仅代表已核实的利润表金额）"
                            if latest.report_currency else "金额币种未核实；仅保留数据源数值，不作金额趋势比较")
            if latest.disclosure_currency:
                amount_basis += f"；目录列报币种：{latest.disclosure_currency}（不等同于金额币种）"
            amount_basis += "；展示缩写为万/亿，不推断上游换汇情况"
        else:
            amount_basis = (f"最新利润表原币：{latest.report_currency}（不代表其他报表币种）" if latest.report_currency
                            else "财报原币，币种未记录") + "；金额以基本货币单位记录，展示为万/亿，未换汇"

        metrics: dict[str, object] = {
            "latest_quarter": latest.fiscal_quarter.isoformat(),
            "revenue": latest.revenue,
            "net_profit": latest.net_profit,
            "total_assets": latest.total_assets,
            "total_equity": latest.total_equity,
            "roe": latest.roe,
            "gross_margin": latest.gross_margin,
            "amount_basis": amount_basis,
            "annual_roe": context.financial_scoring_facts.get("annual_roe", {}),
            **template_metrics,
        }
        if latest.revenue_basis:
            metrics["revenue_basis"] = latest.revenue_basis
        if latest.gross_margin is not None and latest.gross_margin_basis:
            metrics["gross_margin_basis"] = latest.gross_margin_basis
        if latest.operating_cash_flow is not None:
            metrics["operating_cash_flow"] = latest.operating_cash_flow
        elif latest.operating_cash_flow_per_share is not None:
            metrics["operating_cash_flow_per_share"] = latest.operating_cash_flow_per_share

        roe_trend = []
        for d in sorted_data[:8]:
            if d.roe is not None:
                roe_trend.append({"quarter": d.fiscal_quarter.isoformat(), "roe": round(d.roe, 4)})
        metrics["roe_trend"] = roe_trend
        trends = build_financial_trends(financials, context.symbol)
        metrics.update(trends["latest_yoy"])
        metrics["operating_trends"] = trends["highlights"]
        metrics["trend_note"] = trends["note"]
        if latest.operating_cash_flow is not None:
            metrics["cash_flow_source"] = {"reported": "直接披露", "estimated": "推算值（非直接披露）"}.get(
                latest.cash_flow_basis, "期间或币种证据不足")

        status = "ok" if (
            len(sorted_data) >= 4
            and context.sufficiency.financial.level == SufficiencyLevel.SUFFICIENT
        ) else "partial"
        summary = self._build_summary(metrics, status)

        # 数据充足性检查
        if (context.sufficiency and context.sufficiency.financial.level == SufficiencyLevel.INSUFFICIENT
                and not context.financial_scoring_facts):
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="财务数据不足", metrics=metrics,
                                  score=None, score_detail="财务维度数据不足，跳过打分" + ("；" + template_note if template_note else ""))

        # 配置驱动打分
        if config:
            scorer = self._get_scorer(config)
            score, score_detail, risk_flags = scorer.score_financial(context)
            if template_note:
                score_detail = template_note + "；" + score_detail
            basis = getattr(scorer, "financial_score_basis", {})
            metrics["scoring_basis"] = basis
            if context.financial_scoring_facts and score is not None:
                status = "ok" if basis.get("available_max", 0) >= basis.get("eligible_max", 0) > 0 else "partial"
            industry_note = scorer._industry_note() if hasattr(scorer, '_industry_note') else ""
            return AnalysisResult(dimension=self.dimension, status=status,
                                  summary=summary, metrics=metrics,
                                  score=score, score_detail=score_detail,
                                  risk_flags=risk_flags, industry_note=industry_note)

        # 降级：无 config 时返回无分数的结果
        return AnalysisResult(dimension=self.dimension, status=status,
                              summary=summary, metrics=metrics)

    @staticmethod
    def _get_scorer(config: dict[str, Any]):
        strategy_key = config.get("meta", {}).get("strategy_key", "GeneralScorer")
        from analysis.scorers.bank import BankScorer
        from analysis.scorers.cyclical import CyclicalScorer
        from analysis.scorers.general import GeneralScorer
        from analysis.scorers.non_bank_financial import NonBankFinancialScorer
        from analysis.scorers.pharma import PharmaScorer
        from analysis.scorers.real_estate import RealEstateScorer
        from analysis.scorers.tech_growth import TechGrowthScorer

        strategy_map = {
            "GeneralScorer": GeneralScorer,
            "BankScorer": BankScorer,
            "CyclicalScorer": CyclicalScorer,
            "TechGrowthScorer": TechGrowthScorer,
            "RealEstateScorer": RealEstateScorer,
            "NonBankFinancialScorer": NonBankFinancialScorer,
            "PharmaScorer": PharmaScorer,
        }
        scorer_cls = strategy_map.get(strategy_key, GeneralScorer)
        return scorer_cls(config, {})

    def _build_summary(self, metrics: dict, status: str) -> str:
        parts = []
        rev_growth = metrics.get("revenue_growth_yoy")
        if rev_growth is not None:
            direction = "增长" if rev_growth > 0 else "下降"
            parts.append(f"营收同比{direction}{abs(rev_growth)*100:.2f}%")
        profit_growth = metrics.get("profit_growth_yoy")
        if profit_growth is not None:
            direction = "增长" if profit_growth > 0 else "下降"
            parts.append(f"净利润同比{direction}{abs(profit_growth)*100:.2f}%")
        roe = metrics.get("roe")
        if roe is not None and is_finite_number(roe) and is_finite_number(roe * 100):
            parts.append(f"ROE {roe*100:.2f}%")
        return "；".join(parts) if parts else "财务指标数据不足"

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
        financials = context.financial_data or []
        if not financials:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="财务数据不可用", metrics={})

        sorted_data = sorted(financials, key=lambda x: x.fiscal_quarter, reverse=True)
        latest = sorted_data[0]

        metrics: dict[str, object] = {
            "latest_quarter": latest.fiscal_quarter.isoformat(),
            "revenue": latest.revenue,
            "net_profit": latest.net_profit,
            "total_assets": latest.total_assets,
            "total_equity": latest.total_equity,
            "roe": latest.roe,
            "gross_margin": latest.gross_margin,
            "amount_basis": (f"最新利润表原币：{latest.report_currency}（不代表其他报表币种）" if latest.report_currency
                             else "财报原币，币种未记录") + "；金额以基本货币单位记录，展示为万/亿，未换汇",
        }
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
                latest.cash_flow_basis, "来源未记录")

        status = "ok" if (
            len(sorted_data) >= 4
            and context.sufficiency.financial.level == SufficiencyLevel.SUFFICIENT
        ) else "partial"
        summary = self._build_summary(metrics, status)

        # 数据充足性检查
        if context.sufficiency and context.sufficiency.financial.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="财务数据不足", metrics=metrics,
                                  score=None, score_detail="财务维度数据不足，跳过打分")

        # 配置驱动打分
        if config:
            scorer = self._get_scorer(config)
            score, score_detail, risk_flags = scorer.score_financial(context)
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

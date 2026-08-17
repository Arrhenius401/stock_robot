"""财务分析模块 — 配置驱动，无硬编码阈值"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisContext, AnalysisResult, SufficiencyLevel


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

        metrics = {
            "latest_quarter": latest.fiscal_quarter.isoformat(),
            "revenue": latest.revenue,
            "net_profit": latest.net_profit,
            "total_assets": latest.total_assets,
            "total_equity": latest.total_equity,
            "operating_cash_flow": latest.operating_cash_flow,
            "roe": latest.roe,
            "gross_margin": latest.gross_margin,
        }

        if len(sorted_data) >= 2:
            prev_year = sorted_data[-1] if len(sorted_data) >= 5 else sorted_data[1]
            if (latest.revenue is not None and prev_year.revenue is not None
                    and prev_year.revenue > 0):
                metrics["revenue_growth_yoy"] = round(
                    (latest.revenue - prev_year.revenue) / prev_year.revenue, 4)
            if (latest.net_profit is not None and prev_year.net_profit is not None
                    and prev_year.net_profit > 0):
                metrics["profit_growth_yoy"] = round(
                    (latest.net_profit - prev_year.net_profit) / prev_year.net_profit, 4)

        roe_trend = []
        for d in sorted_data[:8]:
            if d.roe is not None:
                roe_trend.append({"quarter": d.fiscal_quarter.isoformat(), "roe": round(d.roe, 4)})
        metrics["roe_trend"] = roe_trend

        status = "partial" if len(sorted_data) < 3 else "ok"
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
            parts.append(f"营收同比{direction}{abs(rev_growth)*100:.1f}%")
        profit_growth = metrics.get("profit_growth_yoy")
        if profit_growth is not None:
            direction = "增长" if profit_growth > 0 else "下降"
            parts.append(f"净利润同比{direction}{abs(profit_growth)*100:.1f}%")
        roe = metrics.get("roe")
        if roe is not None:
            parts.append(f"ROE {roe*100:.1f}%")
        return "；".join(parts) if parts else "财务指标数据不足"

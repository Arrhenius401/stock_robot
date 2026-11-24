"""财务分析模块 — 配置驱动，无硬编码阈值"""
import re
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
        if context.market == "us" and meta.get("scoring_template_source"):
            template_note = (f"评分模板：{meta['scoring_template_source']}；投资风格：{meta.get('style_category', '未记录')}；"
                             f"通用规则回退：{'是' if meta.get('scoring_template_fallback') else '否'}；未做美股历史回测校准")
            template_metrics = {"评分模板": template_note}
        if context.market == "us" and context.financial_special:
            template_metrics.update(context.financial_special)
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
        elif context.market == "us":
            amount_basis = (f"金额币种：{latest.report_currency}" if latest.amount_currency_verified and latest.report_currency
                            else "金额币种未核实") + "；财季报，金额展示为万/亿；不推断换汇、会计准则或 ADR 每股比例；报告期按财季月末展示"
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
            "gross_margin": "不适用（银行/保险）" if latest.gross_margin_basis == "银行/保险不适用" else latest.gross_margin,
            "net_margin": latest.net_margin,
            "amount_basis": amount_basis,
            "annual_roe": context.financial_scoring_facts.get("annual_roe", {}),
            **template_metrics,
        }
        if latest.net_margin is not None and latest.net_margin_basis:
            metrics["net_margin_basis"] = (
                f"{latest.net_margin_basis}；期间 {latest.period_start or '未记录'} 至 {latest.fiscal_quarter}；"
                f"来源 {latest.field_sources.get('net_profit') or latest.financial_source or latest.reporting_basis or '未记录'}"
            )
        metrics["amount_currency_reason"] = latest.amount_currency_reason
        if latest.reported_revenue is not None:
            metrics.update(
                reported_revenue=latest.reported_revenue,
                reported_revenue_ttm=latest.reported_revenue_ttm,
                reported_revenue_basis=latest.reported_revenue_basis,
                reported_revenue_source=latest.reported_revenue_source,
                reported_revenue_period=latest.reported_revenue_period,
            )

        if latest.revenue_basis:
            metrics["revenue_basis"] = latest.revenue_basis
        if latest.gross_margin_basis:
            metrics["gross_margin_basis"] = latest.gross_margin_basis
        if latest.operating_cash_flow is not None:
            metrics["operating_cash_flow"] = latest.operating_cash_flow
        elif latest.operating_cash_flow_per_share is not None:
            metrics["operating_cash_flow_per_share"] = latest.operating_cash_flow_per_share

        from data.financial_ttm import financial_ttm

        profit_ttm = financial_ttm(financials, "net_profit", context.market, require_precision=True)
        equity_row = next((row for row in sorted_data if row.common_equity is not None or row.total_equity is not None), None)
        equity = (equity_row.common_equity if equity_row.common_equity is not None else equity_row.total_equity) if equity_row else None
        if equity_row is not None:
            equity_field = "common_equity" if equity_row.common_equity is not None else "total_equity"
            if equity_row.field_precision.get(equity_field) != "source_native":
                equity = None
        if equity is not None and equity <= 0:
            metrics["roe"] = "净资产为负，ROE 不适用" if equity < 0 else "净资产为零，ROE 不适用"
        elif profit_ttm is None:
            metrics["roe"] = "TTM 不可用"
        else:
            metrics["roe"] = profit_ttm / equity if equity is not None else None
        equity_source = (equity_row.field_sources.get("common_equity") or equity_row.field_sources.get("total_equity") or equity_row.financial_source or "来源未记录") if equity_row else "来源未记录"
        source_date = re.search("[0-9]{4}-[0-9]{2}-[0-9]{2}", equity_source)
        denominator_date = ((source_date.group() if source_date else equity_row.fiscal_quarter.isoformat()) + "；" + equity_source) if equity_row else "缺失"
        metrics["roe_basis"] = f"TTM 归母净利润 / 最新可得期末归母权益，不年化；分母日期/来源：{denominator_date}；TTM 截至 {latest.fiscal_quarter}"

        not_applicable = (latest.gross_margin_basis == "银行/保险不适用"
                          or latest.revenue_basis.startswith("银行：")
                          or context.sw_industry in {"银行", "保险"}
                          or any(word in context.name for word in ("银行", "保险")))
        if not_applicable:
            metrics["gross_margin"] = "不适用（银行/保险）"
            metrics["net_margin"] = "不适用（银行/保险）"
        for field in ("operating_cash_flow", "capital_expenditure", "free_cash_flow"):
            metrics[field] = "不适用（银行/保险）" if not_applicable else financial_ttm(financials, field, context.market, require_precision=True)
        if (context.market == "hk" and latest.capital_expenditure is None
                and latest.free_cash_flow is None):
            metrics["cash_flow_limitation"] = "港股F10现金流仅提供汇总项，资本开支/自由现金流明细不可用"
        if profit_ttm is None and latest.reported_annual_roe is not None:
            metrics["reported_annual_roe"] = latest.reported_annual_roe
            metrics["reported_annual_roe_period"] = (
                latest.reported_annual_roe_period.isoformat() if latest.reported_annual_roe_period else ""
            )
            metrics["reported_annual_roe_source"] = latest.reported_annual_roe_source

        metrics["cash_flow_source"] = latest.financial_source or latest.reporting_basis or "来源未记录"
        metrics["cash_flow_period"] = f"TTM，截至 {latest.fiscal_quarter.isoformat()}；自由现金流 = 经营活动现金流 − 资本开支；缺失不填零"

        if context.market == "hk" and latest.capital_expenditure is None and latest.free_cash_flow is None:
            metrics["cash_flow_period"] = "港股F10现金流仅提供汇总项，资本开支/自由现金流明细不可用"

        roe_trend = []
        for d in sorted_data[:8]:
            if d.roe is not None:
                roe_trend.append({"quarter": d.fiscal_quarter.isoformat(), "roe": round(d.roe, 4)})
        metrics["roe_trend"] = roe_trend
        trends = build_financial_trends(financials, context.symbol)
        metrics.update(trends["latest_yoy"])
        metrics["operating_trends"] = trends["highlights"]
        metrics["trend_note"] = trends["note"]
        metrics["yoy_basis"] = trends.get("yoy_basis", "")
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

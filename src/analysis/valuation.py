"""估值分析模块 — PE/PB/PS 当前值与历史分位"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from analysis.financial import FinancialAnalyzer
from analysis.valuation_rules import valuation_evidence, valuation_snapshot_dates
from data.schemas import AnalysisContext, AnalysisResult, SufficiencyLevel
from utils.numbers import is_finite_number, is_positive_finite

_SCORING_APPLICABILITY_KEYS = ("pe_ttm", "pb")


class ValuationAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["valuation"]:
        return "valuation"

    def analyze(self, context: AnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        val_data = context.valuation_data
        scoring_context = context
        if context.market != "hk" and context.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT:
            # A 股历史充实失败时，旧分位不能复用；本次独立同行估值仍可单项参与。
            scoring_context = context.model_copy(update={"enriched_valuation": None})
        pe_pct, pb_pct, premium = valuation_evidence(scoring_context)
        scoring_evidence = any(value is not None for value in (pe_pct, pb_pct, premium))
        if val_data is None and not scoring_evidence:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="估值数据不可用", metrics={})

        latest = val_data
        metrics: dict[str, Any] = {
            "pe_ttm": latest.pe_ttm if latest else None,
            "pb": latest.pb if latest else None,
            "ps_ttm": latest.ps_ttm if latest else None,
        }

        applicability = {}
        for key in ("pe_ttm", "pb", "ps_ttm"):
            value = metrics[key]
            valid = is_positive_finite(value)
            reason = ("可用于估值比较" if valid else "暂无数据" if value is None
                      else "数值非正，不适用于高低估判断" if is_finite_number(value)
                      else "数据异常，不适用于高低估判断")
            applicability[key] = {"applicable": valid, "reason": reason}
            if value is not None and not is_finite_number(value):
                metrics[key] = None
        metrics["valuation_applicability"] = applicability
        metrics["pe_percentile"], metrics["pb_percentile"], metrics["target_pe_premium"] = pe_pct, pb_pct, premium
        metrics["valuation_notes"] = [
            f"{ {'pe_ttm': 'PE(TTM)', 'pb': 'PB', 'ps_ttm': 'PS(TTM)'}[key]}：{info['reason']}"
            for key, info in applicability.items() if not info["applicable"]
        ]
        if context.market == "hk":
            snapshot_dates = valuation_snapshot_dates(context)
            metrics.update(pe_as_of=snapshot_dates["pe_ttm"], pb_as_of=snapshot_dates["pb"],
                           ps_as_of=snapshot_dates["ps_ttm"])
            metrics["historical_valuation_applicability"] = {
                key: {"applicable": metrics[key] is not None}
                for key in ("pe_percentile", "pb_percentile")
            }
            notes = metrics["valuation_notes"]
            details: list[str] = []
            metrics["valuation_details"] = details
            if latest is not None:
                notes.extend(latest.valuation_notes)
                details.append("PE/PB 当前值按各自字段日期展示；历史分位使用下述曲线参照值，不用其他来源快照排名")
            else:
                details.append("当前 PE/PB 快照未取得；独立同行比较采用本次同源目标值，不回填其他来源快照")
            if latest is not None and latest.ps_ttm is not None:
                details.append(f"PS(TTM)：{latest.ps_source or '来源未记录'}，日期 {latest.ps_as_of or '未记录'}；仅披露，不计入评分")
            if context.enriched_valuation:
                for metric in ("pe", "pb"):
                    summary = getattr(context.enriched_valuation, f"{metric}_history_summary")
                    if summary.sample_count:
                        details.append(
                            f"{metric.upper()} 近一年历史：{summary.source}，{summary.start} 至 {summary.as_of}，"
                            f"有效样本 {summary.sample_count}，参照值 {summary.reference:.2f} 倍，"
                            f"中位数 {summary.median:.2f} 倍，最低 {summary.low:.2f} 倍，最高 {summary.high:.2f} 倍")
                    if summary.reason:
                        notes.append(f"{metric.upper()} 历史分位：{summary.reason}")
            notes.append("历史低分位不等于低风险或值得买；PS 不代表各行业均适用，银行等需另行判断")
        if not any(info["applicable"] for info in applicability.values()) and not scoring_evidence:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="没有适用于高低估判断的估值指标；原始非正值仅作披露",
                                  metrics=metrics, score=None, score_detail="估值不适用，不计分")

        status = "ok" if (
            context.enriched_valuation
            and len(context.enriched_valuation.daily_points) >= 120
            and context.sufficiency.valuation.level == SufficiencyLevel.SUFFICIENT
        ) else "partial"
        # PS 仅披露、不参与估值评分，不能单独把完整估值降为 partial；
        # PE/PB 边界不变，银行无 PE 仍按原逻辑 partial，另行决策。
        if not all(applicability[key]["applicable"] for key in _SCORING_APPLICABILITY_KEYS):
            status = "partial"
        summary = self._build_summary(metrics, historical_reference=context.market == "hk")

        if (context.sufficiency and context.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT
                and not scoring_evidence):
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="估值数据不足", metrics=metrics,
                                  score=None, score_detail="估值维度数据不足，跳过打分")

        if config:
            scorer = FinancialAnalyzer._get_scorer(config)
            score, score_detail, risk_flags = scorer.score_valuation(scoring_context)
            if score is not None and (pe_pct is None or pb_pct is None):
                score_detail += "；PE/PB 历史分位未齐，仅按已验证的有效子项计分；原历史数据覆盖限制保留"
            industry_note = scorer._industry_note() if hasattr(scorer, '_industry_note') else ""
            partial_status = "partial" if (context.sufficiency and context.sufficiency.valuation.level == SufficiencyLevel.PARTIAL) else status
            return AnalysisResult(dimension=self.dimension, status=partial_status, summary=summary,
                                  metrics=metrics, score=score, score_detail=score_detail,
                                  risk_flags=risk_flags, industry_note=industry_note)

        return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                              metrics=metrics)

    @staticmethod
    def _build_summary(metrics: dict, historical_reference: bool = False) -> str:
        parts = []
        pe = metrics.get("pe_ttm")
        if pe is not None:
            parts.append(f"PE(TTM) {pe:.2f}" + ("（不适用）" if not is_positive_finite(pe) else ""))
        pb = metrics.get("pb")
        if pb is not None:
            parts.append(f"PB {pb:.2f}" + ("（不适用）" if not is_positive_finite(pb) else ""))
        pct = metrics.get("pe_percentile")
        if pct is not None:
            subject = "PE曲线参照值" if historical_reference else "PE"
            parts.append(f"{subject}处于历史{pct:.2f}%分位")
        premium = metrics.get("target_pe_premium")
        if premium is not None:
            parts.append(f"同源同行 PE 溢价 {premium:.2f}%")
        return "；".join(parts) if parts else "估值数据不足"

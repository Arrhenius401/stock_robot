"""将现有采集证据映射到报告能力状态。"""
from dataclasses import dataclass
from typing import Literal

from data.schemas import AnalysisContext, AnalysisResult
from report.market_profile import MarketProfile

Coverage = Literal["complete", "partial", "none"]
Reason = Literal[
    "market_policy", "not_collected", "collection_failed", "insufficient_sample",
    "industry_na", "period_unverified", "unavailable",
]
_SOURCE_KEYS = {"technical": "price", "sentiment": "news"}


@dataclass(frozen=True)
class DimensionState:
    applicable: bool
    coverage: Coverage
    reason: Reason | None = None
    detail: str = ""

    @property
    def status(self) -> str:
        if self.reason == "not_collected":
            return "not_collected"
        if not self.applicable:
            return "not_applicable"
        return {"complete": "available", "partial": "partial", "none": "unavailable"}[self.coverage]


def dimension_state(profile: MarketProfile, result: AnalysisResult | None, ctx: AnalysisContext,
                    *, dimension: str = "") -> DimensionState:
    """能力状态仅供展示，不参与评分和覆盖率计算。"""
    key = result.dimension if result is not None else dimension
    if key == "financial" and ctx.market == "us" and (
        (result is not None and result.metrics.get("used_in_score") is False)
        or any(row.gross_margin_basis == "银行/保险不适用" for row in ctx.financial_data or [])
    ):
        return DimensionState(False, "partial", "industry_na", "美股银行/保险专项仅展示，财务未计分")
    if key == "sentiment" and profile.sentiment_policy == "not_collected":
        return DimensionState(False, "none", "not_collected",
                              "本期未采集可评估舆情样本，不能据此判断整体舆情风险。")
    detail = result.summary if result is not None else ""
    record = ctx.collection_records.get(_SOURCE_KEYS.get(key, key))
    if key == "industry" and profile.period_basis == "report_period" and "非港股通" in detail:
        return DimensionState(False, "none", "market_policy", detail)
    if (key == "financial" and profile.financial_special_policy == "facts_only" and result is not None
            and (result.metrics.get("gross_margin_basis") == "银行/保险不适用" or ctx.financial_special)):
        return DimensionState(True, "partial" if ctx.financial_special else "none", "industry_na",
                              "通用指标对银行或保险不适用，专项仅保留已核实的年度事实。")
    if record and record.result_state == "not_applicable":
        return DimensionState(False, "none", "market_policy", detail)
    if result and result.status == "ok":
        return DimensionState(True, "complete")
    coverage: Coverage = "partial" if result and result.status == "partial" else "none"
    if record and (record.result_state == "failed" or record.error_message):
        return DimensionState(True, coverage, "collection_failed", record.error_message or detail)
    if "报告期未确认" in detail or "报告期间未确认" in detail:
        return DimensionState(True, coverage, "period_unverified", detail)
    if "不足" in detail or (record and record.result_state == "empty"):
        return DimensionState(True, coverage, "insufficient_sample", detail)
    return DimensionState(True, coverage, "unavailable", detail)

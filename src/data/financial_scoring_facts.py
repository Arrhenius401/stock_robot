"""独立的完整年度 ROE 依据；不覆盖最新财报，不重复采集三表。"""
import logging
import time
from datetime import date, datetime
from typing import Any

from data.cache import CacheManager
from data.peer_comparison_fetch import COLLECTION_BUDGET, fetch_financial_rows
from data.peer_comparison_rules import finite, iso_date
from data.schemas import AnalysisContext, FinancialData
from utils.numbers import is_finite_number

logger = logging.getLogger(__name__)


def complete_year(start: Any, end: Any) -> bool:
    """按实际起止日识别完整财政年，不强制自然年。"""
    left, right = iso_date(start), iso_date(end)
    if not left or not right or right > datetime.now().astimezone().date().isoformat():
        return False
    return (date.fromisoformat(right) - date.fromisoformat(left)).days in (364, 365)


def annual_roe_fact(ctx: AnalysisContext) -> dict[str, Any]:
    """消费独立事实时再次校验单位、期间与来源；旧快照 ROE 不作替代。"""
    fact = ctx.financial_scoring_facts.get("annual_roe", {})
    if (isinstance(fact, dict) and is_finite_number(fact.get("value"))
            and complete_year(fact.get("period_start"), fact.get("period_end"))
            and fact.get("source") and fact.get("basis") and not fact.get("reason")):
        return fact
    return {"value": None, "period_start": "", "period_end": "", "source": "", "basis": "",
            "reason": fact.get("reason", "未取得明确完整年度 ROE") if isinstance(fact, dict)
            else "年度 ROE 依据格式无效"}


def _reported(ctx: AnalysisContext) -> list[dict[str, Any]]:
    candidates: dict[str, list[FinancialData]] = {}
    for row in ctx.financial_data or []:
        if (row.symbol == ctx.symbol and row.period_basis == "cumulative"
                and complete_year(row.period_start, row.fiscal_quarter)):
            candidates.setdefault(str(row.fiscal_quarter), []).append(row)
    facts = []
    for end, rows in sorted(candidates.items(), reverse=True):
        row = rows[0]
        source = row.field_sources.get("roe") or row.financial_source or row._data_source
        reason = ""
        if len({(r.period_start, r.roe, r.reporting_basis,
                 r.field_sources.get("roe") or r.financial_source or r._data_source) for r in rows}) != 1:
            reason = "同一年度的 ROE 数值或期间口径存在冲突"
        elif not source or not row.reporting_basis:
            reason = "年度 ROE 来源或报告口径未记录"
        elif not is_finite_number(row.roe):
            reason = "该完整年度缺少有效 ROE"
        # 仅对已核验取数字段的 A 股来源说明加权权益，其他来源保留未知。
        weighted = ctx.market == "a-shares" and source in {"同花顺财报", "新浪财报", "东方财富财报"}
        basis = row.reporting_basis + ("·加权权益ROE" if weighted else "·披露 ROE（权益口径未记录）")
        facts.append({"value": None if reason else row.roe, "period_start": str(row.period_start),
                      "period_end": end, "source": source, "basis": basis + "（完整年度，未机械年化）",
                      "reason": reason})
    return facts


def _comparison(ctx: AnalysisContext) -> dict[str, Any] | None:
    comparison = ctx.enriched_industry.comparison if ctx.enriched_industry else {}
    annual = comparison.get("periods", {}).get("annual", {})
    if not complete_year(annual.get("start"), annual.get("end")):
        return None
    metric = next((m for m in annual.get("metrics", []) if m.get("key") == "roe"), {})
    value = finite(metric.get("target"))
    reason = annual.get("reason", "")
    if not annual.get("source") or not annual.get("basis") or metric.get("unit") != "percent":
        reason = reason or "年度 ROE 来源、权益口径或单位未核实"
    elif value is None:
        reason = reason or "该完整年度缺少有效 ROE"
    return {"value": value / 100 if value is not None and not reason else None,
            "period_start": annual["start"], "period_end": annual["end"],
            "source": annual.get("source", ""), "basis": annual.get("basis", ""), "reason": reason}


def _select_latest(facts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """先按实际截止日选年度；同一期的有效值按传入来源优先级选择。"""
    if not facts:
        return None
    newest = max(fact["period_end"] for fact in facts)
    current = [fact for fact in facts if fact["period_end"] == newest]
    valid = [fact for fact in current if fact["value"] is not None and not fact["reason"]]
    if valid and len({fact["period_start"] for fact in valid}) == 1:
        return valid[0]
    fact = dict(current[0])
    fact["value"] = None
    fact["reason"] = (f"最近完整年度 {newest} 的有效 ROE 未取得"
                      + ("；同一截止日的财政年起点存在冲突" if valid else "；" + "；".join(
                          dict.fromkeys(f["reason"] for f in current if f["reason"]))))
    older = [f for f in facts if f["period_end"] < newest and f["value"] is not None and not f["reason"]]
    if older:
        reference = max(older, key=lambda f: f["period_end"])
        fact["reason"] += f"；较早年度 {reference['period_end']} 仅供参考，不代替最近年度计分"
    return fact


class FinancialScoringFactsService:
    def __init__(self, cache: CacheManager | None = None):
        self.cache = cache

    def enrich(self, ctx: AnalysisContext, *, refresh: bool = False) -> AnalysisContext:
        facts = _reported(ctx)
        comparison_fact = _comparison(ctx)
        if comparison_fact is not None:
            facts.append(comparison_fact)
        fact = _select_latest(facts)
        reason = "完整年度 ROE 的期间、来源或口径未核实，不计分"
        financials = [r for r in ctx.financial_data or [] if r.symbol == ctx.symbol]
        production_ready = any((r.financial_source or r._data_source) and r.reporting_basis
                               and r.period_start and r.period_basis != "unknown" for r in financials)
        comparison = ctx.enriched_industry.comparison if ctx.enriched_industry else {}
        if (fact is None or fact["value"] is None) and financials and (production_ready or comparison):
            latest = max(r.fiscal_quarter for r in financials)
            start = f"{min(latest.year, datetime.now().astimezone().year) - 2}-01-01"
            original_deadline = ctx.industry_data._comparison_deadline if ctx.industry_data else None
            deadline = original_deadline if original_deadline is not None else time.monotonic() + COLLECTION_BUDGET
            try:
                rows, errors = fetch_financial_rows([ctx.symbol], ctx.market, start=start,
                    deadline=deadline, cache=self.cache, refresh=refresh)
                candidates = [r for r in rows if r.get("symbol") == ctx.symbol and r.get("annual")
                              and r.get("period_basis") == "cumulative"
                              and complete_year(r.get("start"), r.get("end"))]
                for selected in sorted(candidates, key=lambda r: r["end"], reverse=True):
                    same_end = [r for r in candidates if r["end"] == selected["end"]]
                    value = finite(selected.get("roe"))
                    missing = selected.get("reason", "")
                    if len({(r.get("start"), r.get("roe"), r.get("basis")) for r in same_end}) != 1:
                        missing = "同一年度的 ROE 数值或期间口径存在冲突"
                    if value is None or not selected.get("basis"):
                        missing = missing or "该完整年度缺少有效 ROE 或权益口径"
                    facts.append({"value": value / 100 if value is not None and not missing else None,
                                  "period_start": selected["start"], "period_end": selected["end"],
                                  "source": "东财主要财务指标", "basis": selected.get("basis", ""), "reason": missing})
                fact = _select_latest(facts)
                if errors:
                    reason += "；" + "；".join(errors)
            except Exception as error:  # noqa: BLE001 — 目标指标网络边界失败隔离，不阻断已有财报。
                logger.warning("年度 ROE 补取失败 %s：%s", ctx.symbol, type(error).__name__)
                reason += f"；目标年度补取失败（{type(error).__name__}）"
        if fact is not None and fact["value"] is None and reason != "完整年度 ROE 的期间、来源或口径未核实，不计分":
            fact["reason"] += "；" + reason
        ctx.financial_scoring_facts["annual_roe"] = fact or {
            "value": None, "period_start": "", "period_end": "", "source": "", "basis": "", "reason": reason}
        return ctx

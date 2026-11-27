"""个股网页与会话工具共用的结构化报告。"""
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from data.schemas import AnalysisContext, AnalysisResult
from report.market_profile import build_disclaimer, profile_for
from report.presentation import signal_presentation
from report.quality import build_data_quality, hk_industry_label
from report.scoring import (
    LLM_UNAVAILABLE_PREFIXES,
    SCORING_METHOD,
    compute_price_info,
    compute_score_summary,
    quantitative_commentary,
    with_commentary_fallback,
)
from report.signal import SignalConfig
from report.valuation_dates import stock_date_presentation


def build_stock_payload(
    symbol: str, name: str, results: list[AnalysisResult], commentary: dict[str, str],
    ctx: AnalysisContext, *, no_llm: bool = False,
    signal_cfg: SignalConfig | None = None, llm_available: bool = True,
) -> dict[str, Any]:
    """质量门槛只组装一次，所有入口共享相同事实。"""
    from data.hk_cny import cny_context

    ctx = cny_context(ctx)
    if not ctx.extra_analysis_enabled:
        from analysis.mode import objective_results

        results = objective_results(results)
    profile = profile_for(ctx.market)
    quality = build_data_quality(ctx, results, profile=profile)
    facts_only = quality.get("report_scope") == "facts_only"
    if facts_only:
        if any(result.dimension == "technical" for result in results):
            commentary = {}
        results = [result for result in results if result.dimension != "technical"]
    summary = compute_score_summary(results)
    summary.score_label = quality.get("score_label", "最终综合得分")
    if not quality["has_scored_data"]:
        summary.base_score = None
        summary.final_score = None
    price = compute_price_info(ctx)
    if facts_only:
        price.update(year_high=None, year_low=None, price_position="不适用（事实报告）")
    record = ctx.collection_records.get("price")
    bulk = (commentary.get("bulk") or "").strip()
    if not ctx.extra_analysis_enabled:
        status = "not_requested"
        bulk = ""
    elif not quality["has_analysis_data"]:
        status = "insufficient"
        bulk = "主要数据不足，暂不调用模型生成结论。"
    elif no_llm:
        status = "disabled"
        bulk = quantitative_commentary(summary, "disabled")
    elif not llm_available:
        status = "config"
        bulk = quantitative_commentary(summary, "config")
    elif bulk and not bulk.startswith(LLM_UNAVAILABLE_PREFIXES):
        status = "ok"
    else:
        status = "failed"
        bulk = with_commentary_fallback(commentary, summary, no_llm=False).get("bulk", "")
    signal = None
    from report.research import render_research, resolve_research

    research = resolve_research(ctx, results, commentary, profile=profile, allow_model=(
        ctx.extra_analysis_enabled and not no_llm and llm_available
        and quality["has_analysis_data"] and bool(commentary.get("research"))))
    if research and research.get("model"):
        status = "ok"
    if research:
        if status == "ok" and not research.get("model"):
            status = "failed"
        bulk = render_research(research)
    if quality["signal_allowed"] and signal_cfg is not None and summary.final_score is not None:
        signal = signal_presentation(summary.final_score, signal_cfg)
    if signal is not None and ctx.market == "us":
        signal = {**signal, "basis": signal["basis"] + "；美股规则信号未做历史回测校准，仅研究参考。"}
    if ctx.market == "us":
        industry = ctx.industry_data.industry if ctx.industry_data else "行业数据不足"
    elif ctx.market == "hk":
        industry = hk_industry_label(ctx, results)
    else:
        industry = ctx.sw_industry or (
            ctx.industry_data.industry if ctx.industry_data else "未知")
    return {
        **({key: quality[key] for key in ("report_scope", "assessment_status", "identity_status")}
           if ctx.market in {"a-shares", "hk", "us"} else {}),
        "analysis_mode": "extra" if ctx.extra_analysis_enabled else "objective",
        **({"currency_conversion": ctx.currency_conversion} if ctx.market == "hk" else {}),
        "scoring_method": SCORING_METHOD,
        "symbol": symbol, "code": symbol, "name": name, "market": ctx.market,
        "market_profile": profile.payload(),
        "price": {"bars": price["price_bars"], "start": price["price_start"], "end": price["price_end"],
                  "cache_status": record.cache_status if record else "unknown",
                  "sources": record.sources if record else []},
        "overview": {
            "price_bars": price["price_bars"],
            "latest_close": price["latest_price"], "year_high": price["year_high"],
            "current_price": price["current_price"], "quote_diagnostics": ctx.current_quote,
            "year_low": price["year_low"], "price_position": price["price_position"],
            "change_pct": price["current_price"]["change_pct"] if price["current_price"]["kind"] == "quote" else price["change_pct"],
            "price_start": price["price_start"], "price_end": price["price_end"],
            "industry": industry,
        },
        "score": {"base": summary.base_score if quality["has_scored_data"] else None,
                  "final": summary.final_score if quality["has_scored_data"] else None,
                  "risk_deduction": summary.risk_deduction},
        "score_rows": summary.score_rows if ctx.extra_analysis_enabled else [],
        "dimensions": {r.dimension: {**r.model_dump(mode="json", exclude={"dimension", "charts"}),
                       **({"display": stock_date_presentation(ctx, r.metrics, r.summary)} if r.dimension == "valuation" else {})}
                       for r in results if not (facts_only and r.dimension == "technical")},
        "commentary": bulk, "comments": [bulk] if bulk else [],
        **({"research_commentary": research} if research else {}),
        "commentary_status": status, "signal": signal, "data_quality": quality,
        "pending_verifications": quality["pending_verifications"],
        "disclaimer": build_disclaimer(profile),
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }

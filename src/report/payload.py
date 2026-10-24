"""个股网页与会话工具共用的结构化报告。"""
from datetime import datetime
from typing import Any

from data.schemas import AnalysisContext, AnalysisResult
from report.builder import build_disclaimer
from report.quality import build_data_quality, hk_industry_label
from report.scoring import (
    LLM_UNAVAILABLE_PREFIXES,
    compute_price_info,
    compute_score_summary,
    with_commentary_fallback,
)
from report.signal import SIGNAL_LABELS, SignalConfig, derive_signal
from report.valuation_dates import stock_date_presentation


def build_stock_payload(
    symbol: str, name: str, results: list[AnalysisResult], commentary: dict[str, str],
    ctx: AnalysisContext, *, no_llm: bool = False,
    signal_cfg: SignalConfig | None = None, llm_available: bool = True,
) -> dict[str, Any]:
    """质量门槛只组装一次，所有入口共享相同事实。"""
    quality = build_data_quality(ctx, results)
    summary = compute_score_summary(results)
    price = compute_price_info(ctx)
    bulk = (commentary.get("bulk") or "").strip()
    if not quality["has_analysis_data"]:
        status = "insufficient"
        bulk = "主要数据不足，暂不调用模型生成结论。"
    elif no_llm:
        status = "disabled"
        bulk = "纯数据模式：本次已跳过 AI 调用。"
    elif not llm_available:
        status = "config"
        bulk = "模型未配置或不可用，数据报告已保留。请检查模型配置。"
    elif bulk and not bulk.startswith(LLM_UNAVAILABLE_PREFIXES):
        status = "ok"
    else:
        status = "failed"
        bulk = with_commentary_fallback(commentary, summary, no_llm=False, unscored=ctx.market == "hk").get("bulk", "")
    signal = None
    if quality["signal_allowed"] and signal_cfg is not None and summary.final_score is not None:
        level = derive_signal(summary.final_score, signal_cfg.thresholds)
        action = signal_cfg.actions[level]
        signal = {"level": level, "label": SIGNAL_LABELS[level],
                  "action": action.action, "position": action.position}
    if ctx.market == "hk":
        industry = hk_industry_label(ctx, results)
    else:
        industry = ctx.sw_industry or (
            ctx.industry_data.industry if ctx.industry_data else "未知")
    return {
        "symbol": symbol, "code": symbol, "name": name, "market": ctx.market,
        "overview": {
            "latest_close": price["latest_price"], "year_high": price["year_high"],
            "year_low": price["year_low"], "price_position": price["price_position"],
            "change_pct": price["change_pct"],
            "price_start": price["price_start"], "price_end": price["price_end"],
            "industry": industry,
        },
        "score": {"base": summary.base_score if ctx.market != "hk" and quality["has_scored_data"] else None,
                  "final": summary.final_score if ctx.market != "hk" and quality["has_scored_data"] else None,
                  "risk_deduction": summary.risk_deduction},
        "score_rows": summary.score_rows,
        "dimensions": {r.dimension: {**r.model_dump(mode="json", exclude={"dimension", "charts"}),
                       **({"display": stock_date_presentation(ctx, r.metrics, r.summary)} if r.dimension == "valuation" else {})}
                       for r in results},
        "commentary": bulk, "comments": [bulk] if bulk else [],
        "commentary_status": status, "signal": signal, "data_quality": quality,
        "pending_verifications": quality["pending_verifications"],
        "disclaimer": build_disclaimer(hk=ctx.market == "hk"),
        "generated_at": datetime.now().astimezone().isoformat(),
    }

"""评分计算与报告组装 — CLI 与 API 共用"""
from dataclasses import dataclass, field

from data.schemas import AnalysisContext, AnalysisResult
from report.formatter import format_metric_value
from report.presentation import signal_presentation
from report.signal import SignalConfig
from utils.numbers import is_finite_number

SCORING_METHOD = "five-dimension-v1"
DIM_WEIGHTS = {"financial": 0.30, "technical": 0.20, "valuation": 0.25,
               "industry": 0.20, "sentiment": 0.05}
DIM_LABELS = {"financial": "财务健康", "technical": "技术趋势",
              "valuation": "估值合理", "industry": "行业对比", "sentiment": "舆情风险"}
DIM_WEIGHT_LABELS = {key: f"{weight:.0%}" for key, weight in DIM_WEIGHTS.items()}
SUFFICIENCY_LABEL = {"ok": "充足", "partial": "部分可用", "unavailable": "数据不足"}
LLM_UNAVAILABLE_PREFIXES = ("（LLM 分析暂时不可用", "LLM 分析暂时不可用", "AI 解读不可用")


@dataclass
class ScoreSummary:
    score_rows: list[dict] = field(default_factory=list)
    base_score: float | None = None
    risk_deduction: int = 0
    final_score: float | None = None
    risk_flags: list[str] = field(default_factory=list)
    score_label: str = "最终综合得分"


def participation_score(result: AnalysisResult | None) -> float | None:
    """显式新舆情口径才反向；旧标注和无效分不能进入新总分。"""
    if result is None or result.status == "unavailable":
        return None
    value = result.score
    if value is None or not is_finite_number(value) or not 0 <= value <= 10:
        return None
    if result.dimension == "sentiment":
        metrics = result.metrics
        if (metrics.get("sentiment_scoring_version") != "risk-v1"
                or metrics.get("assessment_status") != "complete"
                or metrics.get("risk_score") != value):
            return None
        return 10 - value
    return value


def compute_score_summary(results: list[AnalysisResult]) -> ScoreSummary:
    """五维按有效原权重归一；风险标签只提示，不额外扣分。"""
    results_map = {result.dimension: result for result in results}
    scores = {key: participation_score(results_map.get(key)) for key in DIM_WEIGHTS}
    has_core = any(value is not None for key, value in scores.items() if key != "sentiment")
    total_weight = sum(DIM_WEIGHTS[key] for key, value in scores.items() if value is not None)
    total = (round(sum(value * DIM_WEIGHTS[key] for key, value in scores.items()
                       if value is not None) / total_weight, 1) if has_core and total_weight else None)
    rows = []
    flags = []
    for key, label in DIM_LABELS.items():
        result = results_map.get(key)
        if result is None:
            continue
        value = scores[key]
        detail = result.score_detail or ""
        if key == "valuation" and value is None and total is not None:
            note = f"估值维度未计分，原 {DIM_WEIGHT_LABELS[key]} 权重已由其余有效维度按原比例重新归一"
            detail = f"{detail}；{note}" if detail else note
        rows.append({"label": label, "score": f"{value:.1f}" if value is not None else "N/A",
                     "raw_score": result.score if value is not None else None,
                     "direction": "risk" if key == "sentiment" else "higher_better",
                     "weight": DIM_WEIGHT_LABELS[key],
                     "sufficiency": SUFFICIENCY_LABEL.get(result.status, result.status),
                     "detail": detail})
        flags.extend(result.risk_flags)
    return ScoreSummary(score_rows=rows, base_score=total, final_score=total,
                        risk_deduction=0, risk_flags=list(dict.fromkeys(flags)))


def compute_price_info(ctx: AnalysisContext) -> dict:
    """从实际采集区间计算高低点与当前位置。"""
    price_data = sorted(ctx.price_data or [], key=lambda point: point.trade_date)
    year_high = max(p.high for p in price_data) if price_data else None
    year_low = min(p.low for p in price_data) if price_data else None
    latest_price = price_data[-1].close if price_data else None
    from data.latest_quote import price_presentation

    current = price_presentation(ctx.current_quote, latest_price, price_data[-1].trade_date.isoformat() if price_data else None)
    change_pct = price_data[-1].change_pct if price_data else None
    if len(price_data) < 20:
        price_position = f"样本不足（{len(price_data)} 个交易日），不计算"
    elif year_high and year_low and latest_price and (year_high - year_low) > 0:
        pct = (latest_price - year_low) / (year_high - year_low) * 100
        price_position = f"{pct:.0f}%"
    else:
        price_position = "暂无"
    return {"year_high": year_high, "year_low": year_low,
            "price_bars": len(price_data),
            "current_price": current,
            "latest_price": latest_price, "price_position": price_position,
            "change_pct": change_pct,
            "price_start": price_data[0].trade_date.isoformat() if price_data else None,
            "price_end": price_data[-1].trade_date.isoformat() if price_data else None}


def quantitative_commentary(summary: ScoreSummary, state: str = "failed") -> str:
    """AI 不可用时只陈述程序结果，不补造经营解释或模型评级。"""
    lines: list[str] = []
    if summary.final_score is not None:
        lines.append(f"{summary.score_label}为 {summary.final_score:.1f}/10。")
    for row in summary.score_rows:
        value = f"{row['score']}/10" if row.get("score") != "N/A" else "未计分"
        lines.append(f"- {row['label']}：{value}。")
    return "\n".join(lines)


def with_commentary_fallback(
    commentary: dict[str, str],
    summary: ScoreSummary,
    no_llm: bool,
) -> dict[str, str]:
    if no_llm:
        return {**commentary, "bulk": quantitative_commentary(summary, "disabled")}
    bulk = (commentary.get("bulk") or "").strip()
    if bulk and not bulk.startswith(LLM_UNAVAILABLE_PREFIXES):
        return commentary
    return {**commentary, "bulk": quantitative_commentary(summary)}


def build_report(symbol: str, name: str, results: list[AnalysisResult],
                 commentary: dict[str, str], ctx: AnalysisContext,
                 no_llm: bool = False, market_env: dict | None = None,
                 signal_cfg: SignalConfig | None = None) -> str:
    """组装完整报告文本（ReportBuilder 渲染）"""
    from report.builder import ReportBuilder
    from report.market_profile import profile_for
    from report.quality import build_data_quality, hk_industry_label

    if not ctx.extra_analysis_enabled:
        from analysis.mode import objective_results

        results = objective_results(results)
        commentary = {}
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
    if not quality["has_analysis_data"]:
        commentary = {}
    if ctx.extra_analysis_enabled and quality["has_analysis_data"]:
        commentary = with_commentary_fallback(commentary, summary, no_llm)
    from report.research import render_research, resolve_research

    research = resolve_research(ctx, results, commentary, profile=profile, allow_model=(
        not no_llm and bool(commentary.get("research")) and quality["has_analysis_data"]))
    if research:
        commentary = {**commentary, "structured_version": "2", "bulk": render_research(research)}
    price_info = compute_price_info(ctx)
    if quality.get("report_scope") == "facts_only":
        price_info.update(year_high=None, year_low=None, price_position="不适用（事实报告）")
    if ctx.market == "us":
        industry = ctx.industry_data.industry if ctx.industry_data else "行业数据不足"
    elif ctx.market == "hk":
        industry = hk_industry_label(ctx, results)
    elif ctx.sw_industry:
        industry = ctx.sw_industry
    elif (ctx.industry_data and ctx.industry_data.industry
          and ctx.industry_data.industry != "未知"):
        industry = f"{ctx.industry_data.industry}（东财口径，申万待补全）"
    else:
        industry = "申万行业待补全（数据源不可用）"

    signal = None
    if signal_cfg is not None and quality["signal_allowed"] and summary.final_score is not None:
        signal = signal_presentation(summary.final_score, signal_cfg)

    builder = ReportBuilder()
    from report.valuation_dates import stock_date_presentation

    valuation = next((r for r in results if r.dimension == "valuation"), None)
    return builder.build(
        extra_analysis_enabled=ctx.extra_analysis_enabled,
        scoring_method=SCORING_METHOD,
        symbol=symbol,
        name=name,
        market=profile.label,
        profile=profile,
        results=results,
        commentary=commentary,
        no_llm=no_llm,
        industry=industry,
        latest_close=price_info["latest_price"],
        current_price=price_info["current_price"],
        year_high=str(format_metric_value("year_high", price_info['year_high'])),
        year_low=str(format_metric_value("year_low", price_info['year_low'])),
        price_position=price_info['price_position'],
        score_rows=summary.score_rows,
        base_score=summary.base_score,
        risk_deduction=summary.risk_deduction,
        final_score=summary.final_score,
        risk_flags=summary.risk_flags,
        market_env=market_env,
        signal=signal,
        data_quality=quality,
        price_start=price_info["price_start"],
        price_end=price_info["price_end"],
        price_bars=price_info["price_bars"],
        valuation_display=stock_date_presentation(ctx, valuation.metrics, valuation.summary) if valuation else None,
    )

"""评分计算与报告组装 — CLI 与 API 共用"""
from dataclasses import dataclass, field

from data.schemas import AnalysisContext, AnalysisResult
from report.signal import SIGNAL_LABELS, SignalConfig, derive_signal

DIM_WEIGHTS = {"financial": 0.30, "technical": 0.20,
               "valuation": 0.25, "industry": 0.25}
DIM_LABELS = {"financial": "财务健康", "technical": "技术趋势",
              "valuation": "估值合理", "industry": "行业对比",
              "sentiment": "舆情风险"}
DIM_WEIGHT_LABELS = {"financial": "30%", "technical": "20%",
                     "valuation": "25%", "industry": "25%",
                     "sentiment": "不计分"}
SUFFICIENCY_LABEL = {"ok": "充足", "partial": "部分可用", "unavailable": "数据不足"}
LLM_UNAVAILABLE_PREFIXES = ("（LLM 分析暂时不可用", "LLM 分析暂时不可用", "AI 解读不可用")


@dataclass
class ScoreSummary:
    score_rows: list[dict] = field(default_factory=list)
    base_score: float = 0.0
    risk_deduction: int = 0
    final_score: float = 0.0
    risk_flags: list[str] = field(default_factory=list)


def compute_score_summary(results: list[AnalysisResult]) -> ScoreSummary:
    """维度加权得分 + 风险扣分"""
    results_map = {r.dimension: r for r in results}
    score_rows = []
    all_risk_flags = []

    base_score = 0.0
    total_weight = 0.0
    for dim, weight in DIM_WEIGHTS.items():
        r = results_map.get(dim)
        if r and r.score is not None:
            base_score += r.score * weight
            total_weight += weight
    if total_weight > 0:
        base_score = round(base_score / total_weight, 1)

    risk_deduction = 0
    for r in results:
        risk_deduction += len(r.risk_flags)
    risk_deduction = min(risk_deduction, 10)
    final_score = max(0, base_score - risk_deduction)

    for dim, label in DIM_LABELS.items():
        r = results_map.get(dim)
        if r:
            score_rows.append({
                "label": label,
                "score": f"{r.score:.1f}" if r.score is not None else "N/A",
                "weight": DIM_WEIGHT_LABELS[dim],
                "sufficiency": SUFFICIENCY_LABEL.get(r.status, r.status),
                "detail": r.score_detail or "",
            })
        all_risk_flags.extend(r.risk_flags if r else [])

    return ScoreSummary(score_rows=score_rows, base_score=base_score,
                        risk_deduction=risk_deduction, final_score=final_score,
                        risk_flags=all_risk_flags)


def compute_price_info(ctx: AnalysisContext) -> dict:
    """从价格序列计算年内高低点与当前位置"""
    price_data = ctx.price_data or []
    year_high = max(p.high for p in price_data) if price_data else None
    year_low = min(p.low for p in price_data) if price_data else None
    latest_price = price_data[-1].close if price_data else None
    change_pct = price_data[-1].change_pct if price_data else None
    if year_high and year_low and latest_price and (year_high - year_low) > 0:
        pct = (latest_price - year_low) / (year_high - year_low) * 100
        price_position = f"{pct:.0f}%"
    else:
        price_position = "暂无"
    return {"year_high": year_high, "year_low": year_low,
            "latest_price": latest_price, "price_position": price_position,
            "change_pct": change_pct}


def _fallback_neutral_commentary(summary: ScoreSummary) -> str:
    """LLM 不可用时的确定性中性解读，避免报告关键章节空白。"""
    available = [row["label"] for row in summary.score_rows if row.get("score") != "N/A"]
    missing = [row["label"] for row in summary.score_rows if row.get("score") == "N/A"]
    available_text = "、".join(available) if available else "暂无"
    missing_text = "、".join(missing) if missing else "无"

    if summary.final_score >= 7:
        tendency = "偏正面"
    elif summary.final_score >= 4:
        tendency = "中性"
    elif summary.final_score > 0:
        tendency = "偏谨慎"
    else:
        tendency = "信息不足"

    lines = [
        "> AI 解读当前不可用，以下为基于量化结果自动生成的中性摘要。",
        "",
        f"1. 综合数据表现{tendency}。当前可用维度：{available_text}；"
        f"缺失或未计分维度：{missing_text}。最终综合得分为 {summary.final_score}/10。",
    ]
    if summary.risk_deduction > 0:
        lines.append(
            f"2. 风险汇总：本次识别到 {len(summary.risk_flags)} 个风险标签，"
            f"风险扣分 {summary.risk_deduction} 分，需优先核对风险标签对应的数据来源与口径。"
        )
    else:
        lines.append("2. 风险汇总：本次未触发明确风险扣分，仍需结合数据时效性和接口完整性复核。")
    lines.append("3. 多风格观察视角：该摘要不包含模型主观推演，仅用于在 AI 不可用时维持报告可读性。")
    return "\n".join(lines)


def _with_commentary_fallback(
    commentary: dict[str, str],
    summary: ScoreSummary,
    no_llm: bool,
) -> dict[str, str]:
    if no_llm:
        return commentary
    bulk = (commentary.get("bulk") or "").strip()
    if bulk and not bulk.startswith(LLM_UNAVAILABLE_PREFIXES):
        return commentary
    return {**commentary, "bulk": _fallback_neutral_commentary(summary)}


def build_report(symbol: str, name: str, results: list[AnalysisResult],
                 commentary: dict[str, str], ctx: AnalysisContext,
                 no_llm: bool = False, market_env: dict | None = None,
                 signal_cfg: SignalConfig | None = None) -> str:
    """组装完整报告文本（ReportBuilder 渲染）"""
    from report.builder import ReportBuilder

    summary = compute_score_summary(results)
    commentary = _with_commentary_fallback(commentary, summary, no_llm)
    price_info = compute_price_info(ctx)
    industry = ctx.industry_data.industry if ctx.industry_data else "未知"

    signal = None
    if signal_cfg is not None:
        level = derive_signal(summary.final_score, signal_cfg.thresholds)
        action = signal_cfg.actions[level]
        signal = {"level": level, "label": SIGNAL_LABELS[level],
                  "action": action.action, "position": action.position}

    builder = ReportBuilder()
    return builder.build(
        symbol=symbol,
        name=name,
        results=results,
        commentary=commentary,
        no_llm=no_llm,
        industry=industry,
        year_high=f"{price_info['year_high']:.2f}" if price_info['year_high'] else "暂无",
        year_low=f"{price_info['year_low']:.2f}" if price_info['year_low'] else "暂无",
        price_position=price_info['price_position'],
        score_rows=summary.score_rows,
        base_score=summary.base_score,
        risk_deduction=summary.risk_deduction,
        final_score=summary.final_score,
        risk_flags=summary.risk_flags,
        market_env=market_env,
        signal=signal,
    )

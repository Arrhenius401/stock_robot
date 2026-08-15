"""评分计算与报告组装 — CLI 与 API 共用"""
from dataclasses import dataclass, field

DIM_WEIGHTS = {"financial": 0.30, "technical": 0.20,
               "valuation": 0.25, "industry": 0.25}
DIM_LABELS = {"financial": "财务健康", "technical": "技术趋势",
              "valuation": "估值合理", "industry": "行业对比",
              "sentiment": "舆情风险"}
DIM_WEIGHT_LABELS = {"financial": "30%", "technical": "20%",
                     "valuation": "25%", "industry": "25%",
                     "sentiment": "不计分"}
SUFFICIENCY_LABEL = {"ok": "充足", "partial": "部分可用", "unavailable": "数据不足"}


@dataclass
class ScoreSummary:
    score_rows: list[dict] = field(default_factory=list)
    base_score: float = 0.0
    risk_deduction: int = 0
    final_score: float = 0.0
    risk_flags: list[str] = field(default_factory=list)


def compute_score_summary(results) -> ScoreSummary:
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


def compute_price_info(ctx) -> dict:
    """从价格序列计算年内高低点与当前位置"""
    price_data = ctx.price_data or []
    year_high = max(p.high for p in price_data) if price_data else None
    year_low = min(p.low for p in price_data) if price_data else None
    latest_price = price_data[-1].close if price_data else None
    if year_high and year_low and latest_price and (year_high - year_low) > 0:
        pct = (latest_price - year_low) / (year_high - year_low) * 100
        price_position = f"{pct:.0f}%"
    else:
        price_position = "暂无"
    return {"year_high": year_high, "year_low": year_low,
            "latest_price": latest_price, "price_position": price_position}


def build_report(symbol, name, results, commentary, ctx,
                 no_llm: bool = False, market_env: dict | None = None) -> str:
    """组装完整报告文本（ReportBuilder 渲染）"""
    from report.builder import ReportBuilder

    summary = compute_score_summary(results)
    price_info = compute_price_info(ctx)
    industry = ctx.industry_data.industry if ctx.industry_data else "未知"

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
    )

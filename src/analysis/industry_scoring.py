"""同行经营分：复用同一比较快照，不重复取数或计入估值倍数。"""
from datetime import date, datetime
from typing import Any

from utils.numbers import is_finite_number, is_valid_percentile

METRIC_WEIGHTS = {"revenue_growth_yoy": 0.4, "roe": 0.4, "net_margin": 0.2}
PERIOD_WEIGHTS = {"annual": 0.4, "latest": 0.6}
METRIC_LABELS = {"revenue_growth_yoy": "营收同比", "roe": "ROE", "net_margin": "净利率"}


def _period_score(period: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"score": None, "metrics": [], "reason": "",
                              "start": period.get("start", ""), "end": period.get("end", ""),
                              "basis": period.get("basis", "")}
    if period.get("reason"):
        result["reason"] = period["reason"]
        return result
    try:
        start = date.fromisoformat(str(period.get("start", "")))
        end = date.fromisoformat(str(period.get("end", "")))
    except (ValueError, TypeError):
        result["reason"] = "报告期间未确认，无法计算经营分"
        return result
    if start > end or end > datetime.now().astimezone().date() or not period.get("basis"):
        result["reason"] = "报告期间或指标口径无效，无法计算经营分"
        return result
    raw_metrics = period.get("metrics", [])
    metrics: dict[str, dict[str, Any]] = {
        str(metric.get("key", "")): metric for metric in raw_metrics if isinstance(metric, dict)
    } if isinstance(raw_metrics, list) else {}
    for key, weight in METRIC_WEIGHTS.items():
        metric: dict[str, Any] = metrics.get(key, {})
        count, percentile = metric.get("sample_count"), metric.get("percentile")
        valid = (metric.get("status") == "ok" and count is not None and percentile is not None
                 and is_finite_number(count) and count >= 5
                 and float(count).is_integer() and is_valid_percentile(percentile)
                 and is_finite_number(metric.get("target")))
        result["metrics"].append({"key": key, "label": METRIC_LABELS[key], "weight": weight,
                                   "sample_count": count, "score": percentile / 10 if valid and percentile is not None else None,
                                   "reason": "" if valid else metric.get("reason") or "有效同行或可比指标不足"})
    valid_metrics = [metric for metric in result["metrics"] if metric["score"] is not None]
    if len(valid_metrics) < 2:
        result["reason"] = "至少两项指标各有五家有效同行，才能计算该期行业分"
        return result
    total_weight = sum(metric["weight"] for metric in valid_metrics)
    result["score"] = sum(metric["score"] * metric["weight"] for metric in valid_metrics) / total_weight
    for metric in result["metrics"]:
        metric["effective_weight"] = metric["weight"] / total_weight if metric["score"] is not None else 0
    return result


def score_industry_comparison(comparison: dict[str, Any]) -> tuple[float | None, str, dict[str, Any]]:
    """先算每期有效子项，再按年度40%/最新60%合成；所有计算保留原精度。"""
    raw = comparison.get("periods", {}) if comparison.get("version") == 1 else {}
    raw = raw if isinstance(raw, dict) else {}
    periods = {key: _period_score(raw[key] if isinstance(raw.get(key), dict) else {}) for key in PERIOD_WEIGHTS}
    evidence: dict[str, Any] = {"version": 1, "periods": periods, "period_weights": {}}
    available = [key for key, period in periods.items() if period["score"] is not None]
    if not available:
        return None, "行业经营分暂无：两期均缺少足够的同期有效指标", evidence
    annual, latest = periods["annual"], periods["latest"]
    same_period = (all(annual.get(key) == latest.get(key) for key in ("start", "end", "basis"))
                   and bool(annual.get("start")))
    if len(available) == 2 and same_period:
        available = ["annual"]
        evidence["same_period"] = True
    total_weight = sum(PERIOD_WEIGHTS[key] for key in available)
    weights = {key: PERIOD_WEIGHTS[key] / total_weight for key in available}
    evidence["period_weights"] = weights
    score = sum(periods[key]["score"] * weight for key, weight in weights.items())
    details = [f"{'年度' if key == 'annual' else '最新期'}经营分 {periods[key]['score']:.2f}，占 {weight:.0%}"
               for key, weight in weights.items()]
    if same_period and len(periods) == 2 and len(available) == 1 and evidence.get("same_period"):
        details.append("年度与最新为同一报告期，只计算一次")
    for key in PERIOD_WEIGHTS:
        if key not in available and periods[key]["score"] is None:
            details.append(f"{'年度' if key == 'annual' else '最新期'}未计分：{periods[key]['reason']}")
    details.append("营收同比/ROE/净利率原权重40%/40%/20%，缺项按有效指标原比例折算")
    return score, "；".join(details), evidence

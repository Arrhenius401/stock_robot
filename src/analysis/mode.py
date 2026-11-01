"""报告分析模式与客观事实投影；不修改采集缓存或历史报告。"""
import re
from typing import Any

from data.schemas import AnalysisResult


def extra_analysis_enabled(config: Any) -> bool:
    """只有明确布尔启用才允许额外分析，缺项或异常类型均关闭。"""
    return config is not None and config.get("analysis.extra_enabled", False) is True


_JUDGMENT_KEYS = {
    "tag", "score", "score_detail", "risk_score", "participation_score", "risk_flags",
    "scoring_basis", "industry_scoring", "sentiment_scoring_version", "assessment_status",
    "assessment_reason", "all_items", "positive_count", "negative_count", "neutral_count",
    "evaluated_count", "trend_strength", "direction_short", "direction_medium",
    "评分模板", "position_coeff", "recommendation", "advice", "pmi_state",
}


def objective_metrics(value: Any) -> Any:
    """去除结构化判断字段，保留数值、来源、新闻原文及数据限制。"""
    if isinstance(value, dict):
        return {key: objective_metrics(item) for key, item in value.items() if key not in _JUDGMENT_KEYS}
    if isinstance(value, list):
        return [objective_metrics(item) for item in value]
    return value


def objective_results(results: list[AnalysisResult]) -> list[AnalysisResult]:
    """复制结果，避免旧规则摘要或意外评分进入客观报告。"""
    output = []
    for result in results:
        metrics = objective_metrics(result.metrics)
        summary = {"ok": "已取得可用数据", "partial": "数据部分可用", "unavailable": "数据不可用"}.get(
            result.status, "数据状态未记录")
        if result.status != "ok":
            # 仅存在摘要中的适用性/失败原因不能因关闭判断而丢失。
            reasons = [part for part in re.split(r"[；，。]", result.summary) if re.search(
                r"不足|缺失|不可用|无有效|未取得|未核实|不支持|非港股通|待补齐|失败|不适用|尚未适配|不能|冲突|没有", part)]
            if reasons:
                summary = "；".join(reasons)
        if result.dimension == "sentiment":
            count = len(metrics.get("news_items") or metrics.get("headlines") or metrics.get("items") or [])
            if result.status == "ok" or count:
                summary = f"本次取得新闻 {count} 条" if count else "本次暂无可展示新闻"
        output.append(result.model_copy(update={"metrics": metrics, "summary": summary,
                                               "score": None, "score_detail": "",
                                               "risk_flags": [], "industry_note": ""}))
    return output

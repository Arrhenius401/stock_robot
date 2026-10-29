"""个股报告展示副本：不修改评分、原始指标或采集诊断。"""
import re
from typing import Any

from report.signal import SIGNAL_LABELS, SignalConfig, derive_signal


def compact_score_detail(detail: str) -> str:
    """保留子项事实和得分，只去掉反复出现的归一化尾句。"""
    return re.sub(r"；有效子项 [\d.]+/[\d.]+ 分，按原相对权重折算为 [\d.]+/10", "", detail)


def signal_presentation(score: float, config: SignalConfig) -> dict[str, str]:
    """以实际配置说明分数映射，不向报告暴露动作或仓位。"""
    level = derive_signal(score, config.thresholds)
    attack, watch = config.thresholds["attack"], config.thresholds["watch"]
    return {"level": level, "label": SIGNAL_LABELS[level], "basis": (
        f"本次综合得分 {score:.1f}/10；按当前配置，≥{attack}分为进攻，"
        f"≥{watch}且<{attack}分为观望，<{watch}分为防御。")}


def _unique_clauses(value: str, seen: set[str]) -> str:
    """同一维度内只保留一次重复限制，保留其余独立原因。"""
    clauses = [part.strip() for part in value.split("；")]
    kept = [part for part in clauses if part and part not in seen]
    seen.update(kept)
    return "；".join(kept)


def compact_quality(quality: dict[str, Any]) -> dict[str, Any]:
    """汇总每维元数据，异常、降级和历史参考仍逐项直出。"""
    dimensions = [item for item in (quality.get("dimensions") or []) if isinstance(item, dict)]
    if not dimensions:
        # 已压缩副本再次传入时保持原值，避免覆盖上游结果。
        return {"summary_rows": quality.get("summary_rows") or [],
                "limitations": quality.get("limitations") or []}
    rows: list[dict[str, Any]] = []
    limitations: list[dict[str, str]] = []
    important = {"缺失原因", "分析限制", "采集限制", "评估说明", "依赖说明",
                 "最新报告字段来源", "估值字段来源"}
    for dimension in dimensions:
        title = str(dimension.get("title") or dimension.get("key") or "未记录")
        fields = [item for item in (dimension.get("fields") or []) if isinstance(item, dict)]
        values = {item.get("label"): item.get("value") for item in fields}
        sample = str(values.get("样本范围") or "未记录")
        if values.get("评估窗口"):
            sample += f"；评估窗口 {values['评估窗口']}"
        if values.get("有效评估"):
            sample += f"；有效评估 {values['有效评估']}"
        status = str(dimension.get("status") or "unavailable")
        rows.append({"key": dimension.get("key", ""), "title": title, "status": status,
                     "sample": sample, "cutoff": str(values.get("数据截止") or "未记录"),
                     "source": str(values.get("完整来源") or values.get("数据来源") or "未记录")})
        seen_clauses: set[str] = set()
        has_reason = False
        for item in fields:
            label, value = str(item.get("label") or ""), str(item.get("value") or "")
            relevant = label in important or item.get("reference_only") is True
            relevant = relevant or (label == "财务包说明" and value not in {"完整来源包", ""})
            if label == "缓存状态" and "过期缓存" in value:
                relevant = True
                detail = value.replace("过期缓存", "").strip(" ·；")
                value = f"使用过期缓存{'（' + detail + '）' if detail else ''}，请结合实际数据截止日判断时效。"
            if not relevant or not value:
                continue
            has_reason = has_reason or label in {"缺失原因", "分析限制", "采集限制", "评估说明"}
            value = _unique_clauses(value, seen_clauses)
            if value:
                limitations.append({"dimension": title, "label": label, "value": value})
        if status not in {"ok", "not_applicable"} and not has_reason:
            limitations.append({"dimension": title, "label": "原因", "value": "未记录"})
    return {"summary_rows": rows, "limitations": limitations}

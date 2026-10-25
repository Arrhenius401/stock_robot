"""指数资金流报告文案，不回写分析标签或参与综合研判。"""
from typing import Any

from report.formatter import format_metric_value
from utils.numbers import is_finite_number


def capital_presentation(section: dict[str, Any]) -> dict[str, str]:
    """仅根据已取得的资金流值描述零、非零与缺失，余额不代替流量。"""
    metrics: dict[str, Any] = section.get("metrics") or {}
    fields = (("north_bound", "北向资金"), ("main_net_inflow", "主力资金"))
    values = [metrics.get(key) for key, _ in fields if is_finite_number(metrics.get(key))]
    if section.get("status") == "unavailable":
        return {"label": "暂无数据", "tone": "gray", "summary": "资金流向：暂无数据"}
    if not values:
        label, tone = "暂无数据", "gray"
    elif all(value == 0 for value in values):
        label, tone = "无净流入或净流出", "gray"
    else:
        # 非零时沿用分析标签，不重新定义混合流向的业务规则。
        label, tone = {"positive": ("流入", "good"), "negative": ("流出", "bad"),
                       "neutral": ("中性", "mid")}.get(str(metrics.get("tag") or ""), ("方向未记录", "gray"))
    parts = []
    for key, name in fields:
        value = metrics.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not is_finite_number(value):
            parts.append(f"{name}：{'暂无数据' if value is None else '数据异常'}")
        elif value == 0:
            parts.append(f"{name}：无净流入或净流出（{format_metric_value(key, value)}）")
        else:
            direction = "净流入" if value > 0 else "净流出"
            parts.append(f"{name}{direction} {format_metric_value(key, abs(value))}")
    return {"label": label, "tone": tone, "summary": "；".join(parts)}

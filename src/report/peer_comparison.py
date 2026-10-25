"""同行对比 Markdown：只格式化共享证据，不重新计算样本或排名。"""
import html
from typing import Any

from utils.numbers import is_finite_number

COMPARISON_METHOD = (
    "每项指标独立筛选，至少 5 家有效同行才给出中位数和数值分位；目标公司不计入样本。",
    "PE、PB 仅正有限值参与统计；经营指标保留负值，缺失不填 0。",
    "经营指标只对同一起止日期、累计或单期及计算口径一致的数据进行统计；不同期间仅供参考。",
    "分位越低表示数值越低，不直接表示经营优劣；相对折价不直接等同于低估。",
)


def _text(value: Any, fallback: str = "未记录") -> str:
    if value is None or value == "":
        return fallback
    return html.escape(str(value), quote=False).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")


def comparison_value(value: Any, unit: str = "") -> str:
    """比较层百分数不复用财务小数比例格式化。"""
    if not is_finite_number(value):
        return "暂无数据" if value is None else "数据异常"
    return f"{value:.2f}" + ("%" if unit == "percent" else "")


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(_text(cell, "") for cell in row) + " |" for row in rows),
    ])


def _block(block: dict[str, Any], *, operating: bool = False) -> list[str]:
    metrics = [item for item in block.get("metrics", []) if isinstance(item, dict)]
    lines = [f"数据来源：{_text(block.get('source'))}"]
    if operating:
        lines.append(f"报告期：{_text(block.get('label'))}；{_text(block.get('start'))} 至 {_text(block.get('end'))}；口径：{_text(block.get('basis'))}")
    else:
        lines.append(f"估值日期：{_text(block.get('as_of'))}")
    if block.get("reason"):
        lines += ["", _text(block["reason"])]
    if metrics:
        rows = []
        for metric in metrics:
            enough = is_finite_number(metric.get("sample_count")) and metric["sample_count"] >= 5
            rows.append([
                metric.get("label", metric.get("key", "指标")),
                comparison_value(metric.get("target"), metric.get("unit", "")),
                comparison_value(metric.get("median"), metric.get("unit", "")) if enough else "样本不足",
                comparison_value(metric.get("percentile"), "percent") if enough else "—",
                f"{metric.get('sample_count', 0)} 家", metric.get("reason", ""),
            ])
        lines += ["", _table(["指标", "目标公司", "同行中位数", "数值分位", "有效同行", "说明"], rows)]
    elif not block.get("reason"):
        lines += ["", "暂无可比较指标"]
    for key in ("notes", "errors"):
        lines.extend(f"- {_text(item)}" for item in block.get(key, []) if item)
    return lines


def format_peer_comparison_markdown(comparison: dict[str, Any], scoring_method: str | None = None) -> str:
    """下载与推送展示估值、两期经营汇总和来源，不展开同行明细。"""
    lines = (["> 行业评分仅采用同期经营指标；估值对比在估值维度使用。"]
             if scoring_method == "five-dimension-v1" else ["> 行业对比不计分。"])
    lines += ["", f"同行范围：{_text(comparison.get('scope'))}；同行总数：{comparison.get('peer_count', 0)} 家。",
             "", "#### 估值对比", ""]
    lines += _block(comparison.get("valuation") or {})
    periods = comparison.get("periods") or {}
    for key, label in (("annual", "完整年度"), ("latest", "最新一期")):
        lines += ["", f"#### 经营表现 · {label}", ""]
        lines += _block(periods.get(key) or {}, operating=True)
    lines += ["", "#### 数据来源与统计口径", ""]
    for note in [*COMPARISON_METHOD, *comparison.get("notes", []), *comparison.get("errors", [])]:
        lines.append(f"- {_text(note)}")
    return "\n".join(lines)

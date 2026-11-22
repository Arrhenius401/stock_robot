"""同行对比 Markdown：只格式化共享证据，不重新计算样本或排名。"""
import html
import re
from typing import Any

from utils.numbers import is_finite_number

COMPARISON_METHOD = (
    "每项指标独立筛选，至少 5 家有效同行才给出中位数和数值分位；目标公司不计入样本。",
    "PE、PB 仅正有限值参与统计；经营指标保留负值，缺失不填 0；只比较相同期间、累计或单期及计算口径的数据。",
    "分位采用低于目标比例加相等值比例的一半，表示数值位置，不直接表示经营优劣；相对折价不直接等同于低估。",
)
_REDUNDANT_COMPARISON_NOTES = {
    "分位表示数值位置，采用低于目标比例加相等值比例的一半，不直接等同经营优劣。",
    "各指标独立统计，至少 5 家有效同行；目标不计入分母。",
}



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


def _block(block: dict[str, Any], *, operating: bool = False, compact: bool = False, marker: str = "") -> list[str]:
    metrics = [item for item in block.get("metrics", []) if isinstance(item, dict)]
    lines = [f"数据来源：{_text(block.get('source'))}"]
    if operating:
        label = _text(block.get("label"))
        period = f"{_text(block.get('start'))} 至 {_text(block.get('end'))}"
        lines.append(f"报告期：{label if period in label else label + '；' + period}；口径：{_text(block.get('basis'))}")
        if block.get("reference_only"):
            lines.append("（仅参考，不计分）")
    else:
        lines.append(f"估值日期：{_text(block.get('as_of'))}")
    metadata = list(lines)
    if compact:
        lines = []
    if block.get("reason"):
        lines += ["", _text(block["reason"])]
    if metrics:
        rows = []
        for metric in metrics:
            enough = is_finite_number(metric.get("sample_count")) and metric["sample_count"] >= 5
            rows.append([
                metric.get("label", metric.get("key", "指标")) + (marker if metric.get("key") == "roe" else ""),
                comparison_value(metric.get("target"), metric.get("unit", "")),
                comparison_value(metric.get("median"), metric.get("unit", "")) if enough else "样本不足",
                comparison_value(metric.get("percentile"), "percent") if enough else "—",
                f"{metric.get('sample_count', 0)} 家", metric.get("reason", ""),
            ])
        headers = ["指标", "目标公司", "同行中位数", "数值分位", "有效同行", "说明"]
        if compact and not operating:
            headers[3] = "数值分位¹⁰"
        if not any(row[-1] for row in rows):
            headers = headers[:-1]
            rows = [row[:-1] for row in rows]
        lines += ["", _table(headers, rows)]
    elif not block.get("reason"):
        lines += ["", "暂无可比较指标"]
    if not operating and block.get("show_source_rows"):
        # 美股允许行情与 F10 主指标混合，逐行展示来源，不能统称为行情。
        target = block.get("target") or {}
        peers = [target, *block.get("peers", [])]
        rows = [[row.get("symbol", "目标"), row.get("source", "未记录"), row.get("pb_source") or "期间未核实"]
                for row in peers if row]
        lines += ["", _table(["代码", "来源", "PB期间（PB_SOURCE）"], rows)]
    if compact:
        if operating:
            start, end = str(block.get("start") or "未记录"), str(block.get("end") or "未记录")
            basis = str(block.get("basis") or "口径未记录")
            label = re.sub(r"[（(]\d{4}-\d{2}-\d{2}[^）)]*[）)]", "", str(block.get("label") or "未记录"))
            lines += ["", f"{marker} {_text(label)}：{start}—{end}；{_text(basis)}。"]
        else:
            lines += ["", "¹⁰ 分位表示数值位置，不直接代表经营优劣。",
                      f"估值日期：{_text(block.get('as_of'))}。"]
        lines += ["", metadata[0]]
        if block.get("reference_only"):
            lines.append("仅参考，不计分。")
    for key in ("notes", "errors"):
        lines.extend(f"- {_text(item)}" for item in block.get(key, []) if item)
    return lines


def format_peer_comparison_markdown(comparison: dict[str, Any], scoring_method: str | None = None, *,
                                    compact: bool = False) -> str:
    """下载与推送展示估值、两期经营汇总和来源，不展开同行明细。"""
    lines = (["> 行业评分仅采用同期经营指标；估值对比在估值维度使用。"]
             if scoring_method == "five-dimension-v1" else ["> 行业对比不计分。"])
    if compact:
        count = comparison.get("peer_count", 0)
        rank = comparison.get("market_cap_rank") or {}
        number, total = rank.get("rank"), rank.get("total")
        known = (type(number) is int and type(total) is int and 1 <= number <= total
                 and type(count) is int and total == count + 1 and rank.get("as_of"))
        rank_text = f"第 {number}／{total} 家" if known else "暂缺"
        lines += ["", _table(["项目", "内容"], [
            ["同行范围", comparison.get("scope")], ["同行数量", f"{count} 家，不含本公司"],
            ["同行市值排名⁹", rank_text], ["估值数据日期", (comparison.get("valuation") or {}).get("as_of")],
            *( [["市值数据日期", rank["as_of"]]] if known else []),
        ]), "", "⁹ 排名含本公司；完整同日市值不足时暂缺。"]
    else:
        lines += ["", f"同行范围：{_text(comparison.get('scope'))}；同行总数：{comparison.get('peer_count', 0)} 家。"]
    lines += ["", "#### 估值对比", ""]
    lines += _block(comparison.get("valuation") or {}, compact=compact)
    periods = comparison.get("periods") or {}
    for key, label in (("annual", "完整年度"), ("latest", "最新一期")):
        if key == "annual" and str(comparison.get("scope", "")).startswith("东财美股") and key not in periods:
            continue
        period = periods.get(key) or {}
        if period.get("reference_only"):
            label = _text(period.get("label"), label)
        lines += ["", f"#### 经营表现 · {label}", ""]
        lines += _block(period, operating=True, compact=compact, marker="¹¹" if key == "annual" else "¹²")
    special = comparison.get("financial_special") or {}
    if special.get("metrics"):
        lines += ["", "#### 银行专项指标", "", *_block(special, operating=True, compact=compact)]
    if compact:
        lines += ["", "各项独立筛选，至少 5 家有效同行；目标公司不计入统计。"]
    else:
        lines += ["", "#### 数据来源与统计口径", ""]
    notes = [*([] if compact else COMPARISON_METHOD), *comparison.get("notes", []), *comparison.get("errors", [])]
    seen = set()
    for note in notes:
        if note in seen or note in _REDUNDANT_COMPARISON_NOTES:
            continue
        seen.add(note)
        lines.append(f"- {_text(note)}")
    return "\n".join(lines)

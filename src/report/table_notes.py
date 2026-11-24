"""数据表展示副本和精简短注，不改变原始分析与评分证据。"""
import re
from typing import Any

from report.formatter import format_metric_value, format_metrics, metric_display_name

_FINANCIAL_DETAILS = {
    "roe_basis", "amount_basis", "amount_currency_reason", "gross_margin_basis", "net_margin_basis",
    "cash_flow_source", "cash_flow_period", "cash_flow_limitation",
    "yoy_basis", "trend_note", "annual_roe", "scoring_basis", "roe_trend", "operating_trends",
}
_MARKERS = {
    "roe": "¹", "gross_margin": "²", "net_margin": "²",
    "operating_cash_flow": "³", "capital_expenditure": "³", "free_cash_flow": "³",
    "revenue_growth_yoy": "⁴", "profit_growth_yoy": "⁴",
    "deducted_profit_growth_yoy": "⁴", "cash_flow_growth_yoy": "⁴",
    "volume_ratio": "⁵", "pe_ttm": "⁶", "pb": "⁶",
    "pe_percentile": "⁷", "pb_percentile": "⁷", "target_pe_premium": "⁸",
}


def compact_valuation_notes(details: list[str]) -> list[str]:
    """删除重复统计展开；保留缺失、过期、派生等影响判断的信息。"""
    output = []
    samples: dict[int, list[str]] = {}
    for item in details:
        text = str(item).strip()
        if not text:
            continue
        if re.match(r"^(PE|PB).*历史[:：]", text) and "中位数" in text and "有效样本" in text:
            continue
        match = re.search(r"(PE|PB).*有效(?:日期)?样本\s*(\d+).*不足\s*(\d+)", text)
        if match:
            samples.setdefault(int(match[2]), []).append(match[1])
            continue
        if re.sub(r"\s+", "", text).startswith(("PE数据日期", "PB数据日期", "PE/PB当前值", "PS(TTM)：暂无数据")):
            continue
        if "历史分位不等于低风险" in text or "历史低分位不等于低风险" in text:
            continue
        if text.startswith("历史分位使用下述曲线参照值"):
            continue
        for clause in text.split("；"):
            clause = clause.strip()
            if clause and clause not in output:
                output.append(clause)
    for count, labels in samples.items():
        output.append(f"⁷ {'、'.join(dict.fromkeys(labels))}历史样本 {count}／120 个，分位暂缺。")
    return list(dict.fromkeys(output))


def prepare_table(dimension: str, metrics: dict[str, Any]) -> dict[str, Any]:
    """统一数值表与短注；格式化后再加角标，避免比例单位丢失。"""
    shown = {}
    notes = []
    for key, value in format_metrics(metrics).items():
        if (dimension == "financial" and key in _FINANCIAL_DETAILS
                or isinstance(value, (list, dict))):
            continue
        if key in {"peer_names", "peer_display_note", "tag"}:
            continue
        if key in {"valuation_applicability", "historical_valuation_applicability"}:
            continue
        label = metric_display_name(key) + _MARKERS.get(key, "")
        shown[label] = format_metric_value(key, value)
    if dimension == "financial":
        if "roe" in metrics:
            notes.append("¹ TTM 归母净利润／期末权益，非加权 ROE。"
                         if "TTM" in str(metrics.get("roe_basis", ""))
                         else "¹ ROE 按本次披露口径展示。")
        if "gross_margin" in metrics or "net_margin" in metrics:
            notes.append("² 利润率按表内报告期口径；缺失不推算。")
            if metrics.get("net_margin_basis"):
                notes.append(str(metrics["net_margin_basis"]))
            gross = str(metrics.get("gross_margin_basis") or "")
            if "营运收入" in gross:
                notes.append("毛利率为派生值，分母为营运收入，可能不同于表内营业额。")
            elif "毛利/" in gross or "毛利／" in gross or "派生" in gross:
                notes.append("毛利率由毛利／收入计算，非直接披露。")
        if any(key in metrics for key in ("operating_cash_flow", "capital_expenditure", "free_cash_flow")):
            notes.append("³ 均为 TTM；自由现金流已扣资本开支。")
            if "推算" in str(metrics.get("cash_flow_source", "")):
                notes.append("现金流为推算值，非直接披露。")
            elif "证据不足" in str(metrics.get("cash_flow_source", "")):
                notes.append("现金流期间或币种证据不足。")
            limitation = str(metrics.get("cash_flow_limitation") or "")
            if limitation:
                notes.append(limitation)
        if metrics.get("yoy_basis"):
            text = str(metrics["yoy_basis"])
            methods = [method for method in ("源披露", "精确金额复算") if method in text]
            notes.append("⁴ 同比采用" + "或".join(methods or ["已校验来源"]) + "。")
        amount = str(metrics.get("amount_basis", ""))
        if amount:
            currency = re.search(r"(?<![A-Z])(CNY|HKD|USD|EUR|JPY|GBP)(?![A-Z])", amount)
            if "未核实" in amount or "未记录" in amount:
                notes.append("金额币种未核实，目录币种不作金额依据。")
            elif currency and ("利润表" in amount or "不代表其他报表" in amount):
                notes.append(f"利润表币种：{currency[1]}；其他报表币种不据此推断。")
            else:
                notes.append(f"金额币种：{currency[1]}。" if currency else "金额沿用源币种。")
        reason = str(metrics.get("amount_currency_reason") or "")
        if reason:
            notes.append(reason)
        limitation = str(metrics.get("trend_note") or "")
        if limitation:
            if "精度" in limitation or "修订" in limitation:
                notes.append("部分同比精度或可比证据不足，未生成。")
            elif "不足" in limitation or "冲突" in limitation:
                notes.append("部分同期样本不足或冲突，不作趋势判断。")
    elif dimension == "technical" and "volume_ratio" in metrics:
        notes.append("⁵ 前复权；量比比较近 5 日与此前 20 日均量。")
    elif dimension == "valuation":
        notes = compact_valuation_notes([*metrics.get("valuation_details", []), *metrics.get("valuation_notes", [])])
        if "target_pe_premium" in metrics:
            notes.append("⁸ 相对有效同行中位数计算，不直接代表高估程度。")
    return {"metrics": shown, "notes": list(dict.fromkeys(notes))}


def valuation_window_notes(cutoff: str) -> list[str]:
    """窗口与计算性质迁入短注，不因截短标题丢失。"""
    notes = []
    for labels, start, end, count in re.findall(
            r"([A-Z、]+)分位区间：([0-9-]+) 至 ([0-9-]+)，(\d+)个有效样本", cutoff):
        notes.append(f"⁷ {labels}：{start}—{end}，{count} 个有效样本。")
    if "派生" in cutoff:
        notes.append("历史估值由本次财务分母派生，仅参考。")
    elif "百度" in cutoff:
        notes.append("历史估值来自百度实测曲线。")
    elif "东财" in cutoff:
        notes.append("历史估值来自东财日频估值历史。")
    if "不保证覆盖上市以来完整历史" in cutoff:
        notes.append("历史区间有限，不代表上市以来完整历史。")
    return notes


def compact_sentiment(metrics: dict[str, Any]) -> str:
    """保留消息、来源和风险语义，压缩重复解释。"""
    from report.formatter import (
        STOCK_NEWS_DISPLAY_LIMIT,
        _news_url,
        sentiment_display_items,
    )
    from report.peer_comparison import _table, _text
    from utils.numbers import is_finite_number

    risk, score = metrics.get("risk_score"), metrics.get("participation_score")
    lines = [_table(["项目", "结果"], [
        ["舆情风险分¹³", f"{risk:.1f}/10" if is_finite_number(risk) else "无法评估"],
        ["综合参与分", f"{score:.1f}/10" if is_finite_number(score) else "暂无，暂不参与综合评分"],
        ["配置权重", "5%"],
        ["评估窗口", f"{metrics.get('window_start') or '未记录'}—{metrics.get('window_end') or '未记录'}"],
        ["有效评估消息", f"{metrics.get('evaluated_count', 0)} 条"],
    ]), "", "¹³ 越高风险越大；有限样本不代表完整舆情。"]
    reason = str(metrics.get("assessment_reason") or "")
    if reason:
        lines += ["", "相关消息信息不足，本期不计分。" if "内容不足以评估" in reason else _text(reason)]
    if risk == 0:
        lines += ["", "有效样本未识别到负面，不代表全市场无风险。"]
    rows = []
    for item in sentiment_display_items(metrics)[:STOCK_NEWS_DISPLAY_LIMIT]:
        state = ("与目标无直接关联" if item.get("related") is False else "不可评估" if item.get("assessable") is False
                 else "有效相关样本" if item.get("related") is True and item.get("assessable") is True else "未评估")
        sources = []
        for link in item.get("links") or [{"url": item.get("url"), "media": item.get("media") or item.get("source")}]:
            label = _text(link.get("media") or link.get("source") or "来源未记录")
            url = _news_url(link.get("url"))
            sources.append(f"[{label}]({url})" if url else label)
        rows.append([str(item.get("publish_date") or item.get("published_at") or "未记录")[:10],
                     item.get("title", ""), "、".join(sources), state])
    if rows:
        lines += ["", "#### 采集样本与来源", "", _table(["日期", "标题／事件", "来源", "评估状态"], rows)]
    return "\n".join(lines)

"""将黄金确定性事实渲染为可独立归档的中文 Markdown。"""

from __future__ import annotations

import html
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

_STATUS = {"ok": "正常", "stale": "旧缓存/过期", "unavailable": "不可用"}
_FIELD_LABELS = {
    "description": "条件", "value": "当前值", "threshold": "门槛", "met": "是否满足",
    "rsi14": "RSI14", "rsi": "RSI", "rsi_lte": "RSI不高于", "atr": "ATR倍数",
    "atr_lte": "偏离不超过ATR倍数", "deviation_atr": "价格偏离（ATR倍数）",
    "abs_deviation_atr": "绝对偏离（ATR倍数）", "lt": "低于", "max_age_days": "最大观察日龄（天）",
    "close": "收盘价", "ma20": "MA20", "slope_pct": "MA60斜率（%）",
    "prior_slope_pct": "五日前MA60斜率（%）", "3y": "三年", "5y": "五年",
    "main": "主趋势", "marginal": "边际变化", "window": "观察日", "direction": "方向",
    }


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [dict(_mapping(item), key=key) for key, item in value.items()]
    return [item for item in _items(value) if isinstance(item, dict)]


def _plain(value: Any, missing: str = "数据缺失") -> str:
    """保留零与负数，不将缺失或非有限值包装为有效事实。"""
    if value is None:
        return missing
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return missing
        return (
            f"{value:.4f}".rstrip("0").rstrip(".")
            if isinstance(value, float)
            else str(value)
        )
    if isinstance(value, dict):
        return (
            "；".join(f"{_FIELD_LABELS.get(key, key)}：{_plain(item, missing)}" for key, item in value.items()
                      if key != "key" or "description" not in value)
            or missing
        )
    if isinstance(value, (list, tuple)):
        return "；".join(_plain(item, missing) for item in value) or missing
    return re.sub(r"[\s\x00-\x1f\x7f]+", " ", str(value)).strip() or missing


def _cell(value: Any, missing: str = "数据缺失") -> str:
    """上游文本一律作为单行文本，HTML 与 Markdown 结构均不透传。"""
    single_line = re.sub(r"[\s\x00-\x1f\x7f]+", " ", _plain(value, missing)).strip()
    text = html.escape(single_line, quote=True)
    text = re.sub(r"([\\`*_\[\](){}#!|~])", lambda match: f"&#{ord(match[0])};", text)
    # 独立正文也不能用行首列表或分隔线改变结构，同时保留负数和日期的可读形式。
    return re.sub(
        r"^(?:[-+](?=\s)|-{3,}$|\d+\.(?=\s))",
        lambda match: "".join(
            f"&#{ord(char)};" if char in "-+." else char for char in match[0]
        ),
        text,
    )


def _label(value: Any, labels: dict[str, str]) -> Any:
    return labels.get(value, value) if isinstance(value, str) else value


def _pct(value: Any) -> str:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        return "数据缺失"
    return f"{_plain(value)}%"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(_cell(item) for item in row) + " |" for row in rows),
        "",
    ]


def _generated_at(value: Any) -> str:
    if isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value)
        except ValueError:
            return _cell(value)
        if timestamp.tzinfo is not None:
            return (
                _cell(timestamp.astimezone(timezone(timedelta(hours=8))).isoformat())
                + "（Asia/Shanghai）"
            )
    return _cell(value, "未提供")


def _number(value: Any, *, decimals: int = 2) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "数据缺失"
    return f"{value:,.{decimals}f}"


def _status(value: Any) -> str:
    return _STATUS.get(value, "状态未提供") if isinstance(value, str) else "状态未提供"


def _coverage_status(value: Any, result_state: Any) -> str:
    status = _status(value)
    return status + "（部分数据）" if value == "ok" and result_state == "partial" else status


def _observation_status(observation: dict[str, Any]) -> str:
    status = _plain(observation.get("state"))
    if observation.get("source_status") == "stale":
        return status + "（历史参考）"
    if observation.get("result_state") == "partial":
        return status + "（部分数据）"
    return status


def _direction(market: dict[str, Any]) -> str:
    if market.get("status") != "ok":
        return "无法判断"
    value = _mapping(market.get("short_term")).get("direction")
    return _plain(value, "无法判断")


def _risk_name(key: Any) -> str:
    return {"oversold": "RSI进入超卖区间", "high_volatility": "波动率处于高位", "historical_high": "历史价格位置较高", "risk_data_missing": "风险数据缺失"}.get(key, "风险数据未识别")


def render_gold_markdown(payload: dict[str, Any]) -> str:
    """生成价格优先的事实简报，不使用 AI 文本或旧版投资结论。"""
    data = _mapping(payload)
    markets = _mapping(data.get("markets"))
    domestic = _mapping(markets.get("domestic"))
    international = _mapping(markets.get("international"))
    pairs = [("国内 Au99.99", domestic), ("国际 XAU（CFD）", international)]
    lines = ["# 黄金市场观察简报", "", "## 黄金价格", "", "| 项目 | 价格 | 当日变动 | 行情日期 |", "| --- | --- | --- | --- |"]
    for label, market in pairs:
        price = _number(market.get("latest_close"))
        unit = _plain(market.get("unit"), "单位未提供")
        shown = f"**{_cell(price)} {_cell(unit)}**" if price != "数据缺失" else "数据缺失"
        lines.append(f"| {label} | {shown} | {_cell(_pct(market.get('daily_change_pct')))} | {_cell(market.get('as_of'))}（{_cell(_coverage_status(market.get('status'), market.get('result_state')))}） |")
    lines += ["", "国际 XAU 为 CFD 参考行情，非官方基准或同步可成交报价；两地行情可能存在时点差异。", ""]
    snapshots = [f"国内技术{_cell(_direction(domestic))}", f"国际技术{_cell(_direction(international))}"]
    for label, market in pairs:
        if market.get("status") == "ok":
            snapshots += [f"{label}{_cell(_risk_name(r.get('key')))}" for r in _records(market.get("risks")) if r.get("active") is True]
    lines += ["快速快照：" + "；".join(snapshots) + "。技术标签未验证择时效果；RSI 超卖不代表见底。", "", "国内金条、国内黄金 ETF 参考国内价格；国际黄金和海外黄金资产参考国际价格。技术状态仅作辅助观察。", "", "## 近期价格变化", ""]
    rows = []
    for label, market in pairs:
        performance = _mapping(market.get("performance"))
        rows.append([label, market.get("as_of"), *[_pct(_mapping(performance.get(window)).get("return_pct")) for window in ("5", "20", "60")], _coverage_status(market.get("status"), market.get("result_state"))])
    lines += _table(["市场", "行情日期", "近5日", "近20日", "近60日", "状态"], rows)
    lines += ["各市场按各自观察日计算，日期不一致时不能视为同步比较。", "", "## 辅助分析", "", "### 当前技术状态", ""]
    for label, market in pairs:
        ind = _mapping(market.get("indicators"))
        reason = _mapping(market.get("short_term")).get("reason") if market.get("status") == "ok" else "行情过期或不可用"
        lines += [f"**{label}**：{_cell(_direction(market))}；原因：{_cell(reason, '未提供')}；收盘价 {_cell(market.get('latest_close'))}，MA20 {_cell(ind.get('ma20'))}，MA60 {_cell(ind.get('ma60'))}，MA60 斜率 {_cell(_pct(ind.get('slope_pct')))}；数据状态：{_cell(_coverage_status(market.get('status'), market.get('result_state')))}。", ""]
    lines += ["技术状态描述历史价格和均线关系，不代表已验证的预测或择时效果。", "", "### 宏观与需求观察", ""]
    observations = _records(data.get("observations"))
    if observations:
        for obs in observations:
            main = _mapping(obs.get("main"))
            marginal = _mapping(obs.get("marginal"))
            window_unit = "个月" if obs.get("key") in ("china_reserves", "global_reserves") else "个观察日"
            value_label = "历史参考值" if obs.get("source_status") == "stale" else "最新值"
            lines += [f"- **{_cell(obs.get('name'))}**：{value_label} {_cell(obs.get('latest_value'))} {_cell(obs.get('unit'), '')}；主窗口 {_cell(main.get('window'))} {window_unit}变化 {_cell(main.get('change'))} {_cell(main.get('unit'), '')}；边际窗口 {_cell(marginal.get('window'))} {window_unit}变化 {_cell(marginal.get('change'))} {_cell(marginal.get('unit'), '')}；观察日期 {_cell(obs.get('as_of'))}，状态 {_cell(_observation_status(obs))}。"]
        lines.append("")
    else:
        lines += ["宏观和需求观察数据缺失。", ""]
    lines += ["实际利率包含市场预期和风险溢价；历史窗口可能滞后于预期变化。中国储备是单一国家的月度滞后样本，SPDR 是北美单只 ETF 样本，不能代表全球央行或全球资金。全球央行数据缺失时不作推断。", "", "### 当前主要风险", ""]
    for label, market in pairs:
        active = [r for r in _records(market.get("risks")) if r.get("active") is True]
        if market.get("status") != "ok":
            desc = "数据不可用或过期，无法判断当前风险"
        elif active:
            desc = "；".join(_risk_name(r.get("key")) for r in active)
        elif market.get("risks"):
            desc = "已提供的风险标签未触发"
        else:
            desc = "风险数据缺失"
        lines += [f"- {label}：{_cell(desc)}。"]
    lines += ["", "### 接下来观察什么", "", "观察两地收盘价与均线、波动及 RSI 的变化，以及实际利率、美元和需求样本的后续更新；这些指标并未验证为有效择时信号。", "", "## 数据覆盖与时效", ""]
    sources = _records(data.get("sources"))
    coverage_rows = []
    limited = []
    for source in sources:
        record = _mapping(source.get("collection_record"))
        status = _coverage_status(source.get("status"), record.get("result_state"))
        name = source.get("name", source.get("key"))
        coverage_rows.append([name, source.get("source"), source.get("as_of"), status])
        if source.get("status") != "ok" or record.get("result_state") == "partial":
            limited.append(f"{_cell(name)}：{_cell(status)}")
    if coverage_rows:
        lines += _table(["数据", "来源", "观察日期", "状态"], coverage_rows)
    else:
        lines += ["来源记录缺失，无法核实数据覆盖与时效。", ""]
    if limited:
        lines += ["**需注意的数据：**" + "；".join(limited) + "。", ""]
    lines += [
        (
            "**数据限制：**国际 XAU 为 CFD 参考行情；国内、国际价格与宏观数据的观察时点可能不同。"
            "中国黄金储备为单一国家的月度样本，SPDR 仅为北美单只 ETF 样本；"
            "全球央行数据缺失时不作推断。数据状态只反映资料完整性，不代表预测概率。"
        ),
        "",
        f"生成时间：{_generated_at(data.get('generated_at'))}；规则版本：{_cell(data.get('rules_version'), '未提供')}。",
        "",
        "分析规则未验证历史收益或预测胜率，不能据此推断未来表现。",
        "",
    ]
    return "\n".join(lines)

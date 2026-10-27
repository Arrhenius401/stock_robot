"""将黄金确定性事实渲染为可独立归档的中文 Markdown。"""

from __future__ import annotations

import html
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

_DEFAULT_GUIDE = (
    "国内金条、黄金ETF看国内部分；国际黄金、海外资产看国际部分。"
    "持有1—3年看长期配置，几周至3个月看短期交易。先看方向与风险，再看依据和重估条件；具体产品另核对成本。"
)
_STATUS = {"ok": "正常", "stale": "旧缓存/过期", "unavailable": "不可用"}
_FIELD_LABELS = {
    "description": "条件", "value": "当前值", "threshold": "门槛", "met": "是否满足",
    "rsi14": "RSI14", "rsi": "RSI", "rsi_lte": "RSI不高于", "atr": "ATR倍数",
    "atr_lte": "偏离不超过ATR倍数", "deviation_atr": "价格偏离（ATR倍数）",
    "abs_deviation_atr": "绝对偏离（ATR倍数）", "lt": "低于", "max_age_days": "最大观察日龄（天）",
    "close": "收盘价", "ma20": "MA20", "slope_pct": "MA60斜率（%）",
    "prior_slope_pct": "五日前MA60斜率（%）", "3y": "三年", "5y": "五年",
    "main": "主趋势", "marginal": "边际变化", "window": "观察日", "direction": "方向",
    "core_score": "核心加权合计", "reference_score": "参考加权合计", "total_score": "加权合计",
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


def _publication(row: dict[str, Any]) -> str:
    value = row.get("published_at")
    precision = row.get("published_precision") or _mapping(row.get("metadata")).get("published_precision")
    if precision == "date" and isinstance(value, str):
        return value.split("T")[0] + "（来源当地日期）"
    return str(value) if value else "发布日期未记录"


def _summary(payload: dict[str, Any]) -> list[str]:
    long_term = _mapping(payload.get("long_term"))
    lines = ["## 结论摘要", "", (f"长期（1—3年）：{_cell(long_term.get('direction'), '无法判断')}；"
             f"证据支撑度：{_cell(long_term.get('confidence'), '未提供')}。"), ""]
    markets = _mapping(payload.get("markets"))
    rows = []
    for key, label in (("domestic", "国内黄金"), ("international", "国际黄金")):
        market = _mapping(markets.get(key))
        short = _mapping(market.get("short_term"))
        rows.append([label, short.get("direction", "无法判断"), short.get("confidence"),
                     _mapping(market.get("allocation")).get("label"),
                     _mapping(market.get("trading")).get("label")])
    lines += _table(["市场", "短期方向", "短期证据支撑度", "长期配置建议", "短期交易参考"], rows)
    return lines + ["短期交易仅为技术面参考，不构成长期配置依据。风险、原因与量化重估条件见各市场正文。", ""]


def _long_term(section: dict[str, Any]) -> list[str]:
    lines = [
        "## 长期方向与证据",
        "",
        f"长期（1—3 年）：{_cell(section.get('direction'), '无法判断')}。",
        "",
        f"理由：{_cell(section.get('reason'), '未提供')}。",
        "",
        (
            f"证据支撑度：{_cell(section.get('confidence'), '未提供')}；"
            f"原因：{_cell(section.get('confidence_reasons'), '未提供')}。"
        ),
        "",
        f"缺项与限制：{_cell(section.get('missing'), '无其他必需缺项；需求样本局限见证据支撑度')}。",
        "",
        "### 双窗口加权结果",
        "",
    ]
    windows = _mapping(section.get("windows"))
    rows = []
    for key, label, default in (("main", "主趋势", 120), ("marginal", "边际变化", 60)):
        window = _mapping(windows.get(key))
        rows.append(
            [
                label,
                window.get("window", default),
                window.get("direction", "无法判断"),
                window.get("core_score"),
                window.get("reference_score"),
                window.get("total_score"),
            ]
        )
    lines += _table(
        ["窗口", "有效观察日", "方向", "核心加权合计", "参考加权合计", "合计"], rows
    )
    lines += [
        (
            "分数仅用于审计规则，不是投资评分。120/60日为本源有效观察日口径；月度储备使用6/3个月窗口。"
            "全球与中国属于同一官方储备参考组，合计最多一票。"
        ),
        "",
        "### 因子事实与来源",
        "",
    ]
    factors = _records(section.get("factors"))
    if not factors:
        lines += ["因子数据缺失，无法核对双窗口依据。", ""]
    for factor in factors:
        role = _label(factor.get("role"), {"core": "核心", "reference": "参考"})
        lines += [
            f"#### {_cell(factor.get('name', factor.get('key')))}",
            "",
            (
                f"角色：{_cell(role)}；权重：{_cell(factor.get('weight'))}；"
                f"状态：{_cell(_label(factor.get('state'), _STATUS))}。"
            ),
            "",
            (
                f"来源：{_cell(factor.get('source'))}；来源地址：{_cell(factor.get('source_url'))}；"
                f"原始单位：{_cell(factor.get('unit'))}。"
            ),
            "",
            (
                f"观察日期：{_cell(factor.get('as_of'))}；"
                f"发布日期：{_cell(_publication(factor))}。"
            ),
            "",
        ]
        rows = []
        rank_rows = []
        for key, label in (("main", "主趋势"), ("marginal", "边际变化")):
            item = _mapping(factor.get(key))
            rows.append(
                [
                    label,
                    item.get("window"),
                    item.get("start"),
                    item.get("end"),
                    item.get("count"),
                    item.get("change"),
                    item.get("unit"),
                    item.get("threshold", "数量增减" if factor.get("role") == "参考" else "数据缺失"),
                    item.get("signal", "数据缺失"),
                    item.get("significance", item.get("signal", "数据缺失")),
                ]
            )
            rank_rows.append(
                [
                    label,
                    _pct(item.get("percentile")),
                    item.get("rank_start"),
                    item.get("rank_end"),
                    item.get("rank_count"),
                    item.get("percentile_status"),
                ]
            )
        lines += _table(
            [
                "窗口",
                "长度",
                "开始",
                "结束",
                "观察点数",
                "变化",
                "变化单位",
                "判定阈值（同变化单位）",
                "基础方向",
                "显著性",
            ],
            rows,
        )
        lines += _table(
            [
                "窗口",
                "五年变化分位",
                "分位样本开始",
                "分位样本结束",
                "比较窗口数",
                "分位状态",
            ],
            rank_rows,
        )
    lines += [
        (
            "变化分位比较同长度历史滚动变化，窗口可能重叠，不是独立试验。"
            "月度官方储备是滞后指标，仅用于长期趋势参考；中国数量变化只代表中国样本。"
            "北美 ETF 资金样本（SPDR）不代表全球资金，也不表示持有人全部来自北美。"
        ),
        "",
    ]
    return lines


def _guidance(market: dict[str, Any], long_term: dict[str, Any]) -> list[str]:
    allocation = _mapping(market.get("allocation"))
    trading = _mapping(market.get("trading"))
    short = _mapping(market.get("short_term"))
    lines = [
        "### 长期配置建议（1—3 年）",
        "",
        (
            f"建议：{_cell(allocation.get('label'), '无法给出建议')}。"
            f"理由：{_cell(allocation.get('reason'), '未提供')}。"
        ),
        "",
        (
            f"依据的证据支撑度：长期{_cell(long_term.get('confidence'), '未提供')}；"
            f"本市场短期{_cell(short.get('confidence'), '未提供')}。"
        ),
        "",
        f"重查条件：{_cell(allocation.get('conditions'), '未提供')}。",
        "",
    ]
    if allocation.get("batch_guidance"):
        lines += [f"条件性分批原则：{_cell(allocation.get('batch_guidance'))}", ""]
    lines += [
        "### 短期交易参考（几周至3个月）",
        "",
        (
            f"参考：{_cell(trading.get('label'), '无法给出参考')}。"
            f"理由：{_cell(trading.get('reason'), '未提供')}。"
        ),
        "",
        f"本市场短期证据支撑度：{_cell(short.get('confidence'), '未提供')}。",
        "",
        f"重查条件：{_cell(trading.get('conditions'), '未提供')}。",
        "",
        (
            "仅为技术面参考，不构成长期配置依据。"
            f"适用范围：{_cell(trading.get('scope'), '未提供')}。"
        ),
        "",
    ]
    return lines


def _market(key: str, market: dict[str, Any], long_term: dict[str, Any]) -> list[str]:
    label = "国内黄金" if key == "domestic" else "国际黄金"
    short = _mapping(market.get("short_term"))
    lines = [
        f"## {label}",
        "",
        (
            f"名称：{_cell(market.get('name', label))}；标识：{_cell(market.get('symbol'))}；"
            f"状态：{_cell(_label(market.get('status'), _STATUS))}。"
        ),
        "",
        (
            f"实际交易日期：{_cell(market.get('as_of'))}；收盘：{_cell(market.get('latest_close'))} "
            f"{_cell(market.get('unit'))}；日涨跌：{_pct(market.get('daily_change_pct'))}。"
        ),
        "",
        f"来源：{_cell(market.get('source'))}；来源地址：{_cell(market.get('source_url'))}。",
        "",
        (
            f"短期（几周至3个月）：{_cell(short.get('direction'), '无法判断')}。"
            f"理由：{_cell(short.get('reason'), '未提供')}。"
        ),
        "",
        (
            f"证据支撑度：{_cell(short.get('confidence'), '未提供')}；"
            f"原因：{_cell(short.get('confidence_reasons'), '未提供')}。"
        ),
        "",
    ]
    if key == "international":
        lines += ["新浪 XAU 为 CFD 参考行情，非官方黄金基准或同步可成交报价。", ""]
    lines += _guidance(market, long_term)
    lines += ["### 本市场表现与技术指标", ""]
    performance = _mapping(market.get("performance"))
    rows = []
    for length in ("5", "20", "60", "120", "250"):
        item = _mapping(performance.get(length))
        rows.append(
            [
                length,
                _pct(item.get("return_pct")),
                item.get("start"),
                item.get("end"),
                item.get("count"),
                item.get("status"),
            ]
        )
    lines += _table(["本市场观察日", "涨跌", "开始", "结束", "观察点数", "状态"], rows)
    indicators = _mapping(market.get("indicators"))
    unit = market.get("unit", "原币单位未提供")
    rows = [
        [name, indicators.get(field), measure]
        for field, name, measure in (
            ("ma20", "MA20", unit),
            ("ma60", "MA60", unit),
            ("slope_pct", "MA60二十日斜率", "%"),
            ("rsi14", "Wilder RSI14", "指标值"),
            ("atr14", "ATR14", unit),
            ("vol20_pct", "20日年化波动率", "%"),
            ("vol_percentile", "波动分位", "%"),
            ("max_drawdown_pct", "近252日最大回撤", "%"),
            ("annual_vol_pct", "近252日年化波动率", "%"),
        )
    ]
    lines += _table(["指标", "当前值", "单位"], rows)
    lines += [
        (
            f"波动分位实际样本期间：{_cell(indicators.get('vol_rank_period'))}；"
            f"起止：{_cell(indicators.get('vol_rank_start'))} 至 {_cell(indicators.get('vol_rank_end'))}；"
            f"比较样本数：{_cell(indicators.get('vol_rank_count'))}。"
        ),
        "",
    ]
    lines += ["### 3年与5年历史价格位置", ""]
    positions = _mapping(market.get("price_position"))
    rows = []
    for window, label in (("3y", "3年"), ("5y", "5年")):
        item = _mapping(positions.get(window))
        rows.append(
            [
                label,
                _pct(item.get("percentile")),
                item.get("start"),
                item.get("end"),
                item.get("count"),
                item.get("status"),
            ]
        )
    lines += _table(["区间", "历史价格分位", "开始", "结束", "观察点数", "状态"], rows)
    lines += [
        "历史位置仅描述相对历史价格，不能推断预期回报；位置和波动风险不自动反转多空方向。",
        "",
        "### 风险与量化重估",
        "",
    ]
    risks = _records(market.get("risks"))
    if risks:
        lines += _table(
            ["风险", "当前值", "触发门槛", "状态"],
            [
                [
                    row.get("message", row.get("key")),
                    {k: _mapping(v).get("percentile") for k, v in positions.items()}
                    if row.get("key") == "historical_high" else {"风险数据完整": row.get("value")}
                    if row.get("key") == "risk_data_missing" else row.get("value"),
                    {"要求数据完整": row.get("threshold")} if row.get("key") == "risk_data_missing" else row.get("threshold"),
                    "已触发"
                    if row.get("active") is True
                    else "未触发"
                    if row.get("active") is False
                    else "未提供",
                ]
                for row in risks
            ],
        )
    else:
        lines += ["风险数据缺失，不能据此认定风险门槛通过。", ""]
    reassessment = _records(market.get("reassessment"))
    if reassessment:
        lines += _table(
            ["重估条件", "当前值", "解除/改善门槛", "是否满足"],
            [
                [
                    row.get("description", row.get("key")),
                    row.get("value"),
                    row.get("threshold"),
                    "已满足"
                    if row.get("met") is True
                    else "未满足"
                    if row.get("met") is False
                    else "未提供",
                ]
                for row in reassessment
            ],
        )
    else:
        lines += ["量化重估条件未提供。", ""]
    lines += [
        "解除门槛通过后仍须复核方向、数据日期与其余风险，不自动构成新增买入信号。",
        "",
    ]
    return lines


def _fx(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## 人民币换汇参考影响拆分",
        "",
        (
            "非同步日线参考比较：汇率为纽约中午参考价，黄金为各自日线收盘。"
            "这是会计式参考分解，不是因果归因，也不是可交易的实时境内溢价或套利收益。"
        ),
        "",
    ]
    if not rows:
        return lines + [
            "共同观察日或单位数据缺失，未计算参考拆分；原币表现见各市场。",
            "",
        ]
    for row in rows:
        lines += [
            f"### 近{_cell(row.get('window'))}个共同观察日",
            "",
            (
                f"起止：{_cell(row.get('start'))} 至 {_cell(row.get('end'))}；"
                f"共同观察点数：{_cell(row.get('count'))}。"
            ),
            "",
        ]
        lines += _table(
            [
                "国际黄金涨跌（%）",
                "汇率变化（%）",
                "国内黄金涨跌（%）",
                "换汇参考涨跌（%）",
            ],
            [
                [
                    row.get("international_return_pct"),
                    row.get("fx_return_pct"),
                    row.get("domestic_return_pct"),
                    row.get("reference_return_pct"),
                ]
            ],
        )
        lines += _table(
            [
                "交互项（百分比点）",
                "国际价格参考贡献（百分比点）",
                "汇率参考贡献（百分比点）",
                "未解释差额（百分比点）",
            ],
            [
                [
                    row.get("interaction_pp"),
                    row.get("international_contribution_pp"),
                    row.get("fx_contribution_pp"),
                    row.get("residual_pp"),
                ]
            ],
        )
    return lines + [
        (
            "交互项=g×f；国际与汇率参考贡献分别为g+½gf、f+½gf，二者与未解释差额合计为国内涨跌。"
            "未解释差额可能包含时点、报价、基差等差异，不能直接解释为供需贡献。未前向填充观察日期。"
        ),
        "",
    ]


def _sources(payload: dict[str, Any]) -> list[str]:
    lines = ["## 数据来源与限制", ""]
    sources = _records(payload.get("sources"))
    if sources:
        lines += _table(
            ["系列/来源", "来源地址", "观察日期", "发布日期", "采集时间", "读取器", "单位", "状态", "备注"],
            [
                [
                    row.get("name", row.get("key")),
                    row.get("source_url"),
                    row.get("as_of"),
                    _publication(row),
                    row.get("fetched_at"),
                    row.get("parser"),
                    row.get("unit"),
                    _label(row.get("status"), _STATUS),
                    [item for item in [row.get("source"), *_items(row.get("notes")), row.get("error")] if item],
                ]
                for row in sources
            ],
        )
    else:
        lines += ["来源清单未提供，不能核对来源覆盖与新鲜度。", ""]
    lines += [
        f"本次限制与说明：{_cell(payload.get('notes'), '未提供')}。",
        "",
        (
            "证据支撑度反映覆盖、新鲜度与代表性，未经过预测有效性验证。"
            "本版规则是版本化研究假设，不承诺收益或预测准确率。读取缓存时间不替代实际观察日期；"
            "过期与刷新失败保留原数据日期，缺失项不填零。"
        ),
        "",
    ]
    return lines


def render_gold_markdown(payload: dict) -> str:
    """展示共享 payload 的原有事实，不重算或覆写研判结果。"""
    guide = _plain(payload.get("reading_guide"), _DEFAULT_GUIDE)
    # 超长自定义指南采用固定指南，避免截断可能含有关键适用条件的句子。
    if len(guide) > 100:
        guide = _DEFAULT_GUIDE
    lines = [
        f"# {_cell(payload.get('name'), '黄金综合研报')}",
        "",
        (
            f"生成时间：{_generated_at(payload.get('generated_at'))}；"
            f"规则版本：{_cell(payload.get('rules_version'), '未提供')}。"
        ),
        "",
        "## 阅读指南",
        "",
        _cell(guide),
        "",
        (
            "具体产品应按实际跟踪标的、币种与汇率对冲敞口选择参考市场，并另核对成本；"
            "本报告不分析具体金条或ETF产品。"
        ),
        "",
    ]
    long_term = _mapping(payload.get("long_term"))
    lines += _summary(payload)
    lines += _long_term(long_term)
    markets = _mapping(payload.get("markets"))
    for key in ("domestic", "international"):
        lines += _market(key, _mapping(markets.get(key)), long_term)
    lines += _fx(_records(payload.get("fx_decomposition")))
    lines += _sources(payload)
    status = payload.get("commentary_status")
    lines += ["## AI 辅助解释状态", ""]
    if status == "disabled":
        lines += ["AI 已禁用；方向、风险、两套指引与阈值由确定性规则完整提供。", ""]
    elif status == "failed":
        lines += ["AI 解释失败；以上确定性结论仍然有效，缺项和限制仍按原规则展示。", ""]
    elif status == "unavailable":
        lines += ["AI 未配置或不可用；方向、风险与两套指引仍由确定性规则完整提供。", ""]
    elif payload.get("commentary"):
        lines += [
            "以下为辅助解释，不改变上述计算事实、档位、权重与证据支撑度。",
            "",
            _cell(payload["commentary"]),
            "",
        ]
    else:
        lines += ["AI 状态未提供或未返回解释；确定性事实与结论见上文。", ""]
    return "\n".join(lines).rstrip() + "\n"

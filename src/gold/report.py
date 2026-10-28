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


def _publication(row: dict[str, Any]) -> str:
    value = row.get("published_at")
    precision = row.get("published_precision") or _mapping(row.get("metadata")).get("published_precision")
    if precision == "date" and isinstance(value, str):
        return value.split("T")[0] + "（来源当地日期）"
    return str(value) if value else "发布日期未记录"


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
    lines += ["", "### 接下来观察什么", "", "观察两地收盘价与均线、波动及 RSI 的变化，以及实际利率、美元和需求样本的后续更新；这些指标并未验证为有效择时信号。", "", "## 附录 A：宏观与需求数据", ""]
    factor_rows = []
    for obs in observations:
        for window in ("main", "marginal"):
            part = _mapping(obs.get(window))
            factor_rows.append([obs.get("name"), "主窗口" if window == "main" else "边际窗口", (str(part.get("window")) + ("个月" if obs.get("key") in ("china_reserves", "global_reserves") else "个观察日")) if part.get("window") is not None else None, part.get("change"), part.get("unit"), part.get("start"), part.get("end"), part.get("count"), part.get("percentile"), part.get("percentile_status"), part.get("rank_start"), part.get("rank_end"), part.get("rank_count"), obs.get("latest_value"), obs.get("unit"), obs.get("as_of"), obs.get("source"), obs.get("source_url"), _observation_status(obs)])
    lines += _table(["指标", "窗口", "窗口长度", "变化", "单位", "开始", "结束", "样本数", "历史分位", "分位状态", "分位开始", "分位结束", "分位样本数", "最新值", "最新值单位", "观察日期", "来源", "来源地址", "状态"], factor_rows)
    lines += ["分位仅描述历史位置，具体样本范围见上表；实际利率与美元变化的分位按变化取反计算，下降越大分位越高。缺失或过期数据不补零。", "", "## 附录 B：价格与技术数据", ""]
    for label, market in pairs:
        lines += [f"### {label}", ""]
        perf = _mapping(market.get("performance"))
        lines += _table(["观察日数", "涨跌", "开始", "结束", "样本数", "状态"], [[n, _pct(_mapping(perf.get(n)).get("return_pct")), _mapping(perf.get(n)).get("start"), _mapping(perf.get(n)).get("end"), _mapping(perf.get(n)).get("count"), _mapping(perf.get(n)).get("status")] for n in ("5", "20", "60", "120", "250")])
        ind = _mapping(market.get("indicators"))
        fields = [("ma20", "MA20"), ("ma60", "MA60"), ("slope_pct", "MA60 斜率（%）"), ("prior_slope_pct", "此前 MA60 斜率（%）"), ("rsi14", "Wilder RSI"), ("atr14", "ATR"), ("deviation_atr", "价格偏离（ATR 倍数）"), ("vol20_pct", "年化波动率（%）"), ("vol_percentile", "波动历史分位"), ("vol_rank_start", "波动分位样本开始"), ("vol_rank_end", "波动分位样本结束"), ("vol_rank_count", "波动分位样本数")]
        lines += _table(["指标", "值"], [[name, ind.get(field)] for field, name in fields])
        positions = _mapping(market.get("price_position"))
        lines += _table(["价格位置", "历史分位", "开始", "结束", "样本数", "状态"], [[label2, _mapping(positions.get(key)).get("percentile"), _mapping(positions.get(key)).get("start"), _mapping(positions.get(key)).get("end"), _mapping(positions.get(key)).get("count"), _mapping(positions.get(key)).get("status")] for key, label2 in (("3y", "3年"), ("5y", "5年"))])
    lines += ["历史价格分位仅表示历史位置，不代表高估、低估或未来收益。", "", "## 附录 C：风险数据", ""]
    risk_rows = []
    for label, market in pairs:
        for risk in _records(market.get("risks")):
            active = risk.get("active")
            risk_rows.append([label, _risk_name(risk.get("key")), risk.get("value"), risk.get("threshold"), "已触发" if active is True else "未触发" if active is False else "数据缺失", _coverage_status(market.get("status"), market.get("result_state"))])
    lines += _table(["市场", "风险", "当前值", "参考门槛", "状态", "行情状态"], risk_rows)
    lines += ["门槛是未验证的描述规则；超卖可能伴随反弹，但不改变下跌趋势。", ""]
    fx_lines = _fx(_records(data.get("fx_decomposition")))
    fx_lines[0] = "## 附录 D：汇率参考拆分"
    lines += fx_lines
    lines += ["## 附录 E：来源与局限", ""]
    source_rows = []
    for source in _records(data.get("sources")):
        record = _mapping(source.get("collection_record"))
        source_rows.append([source.get("name", source.get("key")), source.get("source"), source.get("url", source.get("source_url")), source.get("as_of"), _publication(source), source.get("fetched_at"), _coverage_status(source.get("status"), record.get("result_state")), source.get("error"), source.get("notes"), source.get("parser"), source.get("count")])
    lines += _table(["数据", "来源", "地址", "观察日期", "发布日期", "获取时间", "状态", "错误", "备注", "解析器", "样本数"], source_rows)
    rules = _mapping(data.get("rules_snapshot"))
    rule_keys = ("ma_fast", "ma_slow", "ma_slope_window", "flat_slope_pct", "rsi_period", "atr_period", "vol_window", "annual_days", "rsi_oversold", "vol_high_percentile", "price_high_percentile")
    lines += _table(["规则参数", "本报告取值"], [[key, rules.get(key)] for key in rule_keys])
    lines += [f"生成时间：{_generated_at(data.get('generated_at'))}；规则版本：{_cell(data.get('rules_version'), '未提供')}。", "", "数据状态与覆盖程度只反映资料完整性，不代表预测概率。价格、汇率和宏观数据可能滞后；各来源并非同步。分析规则未验证历史收益或预测胜率，不能据此推断未来表现。", ""]
    return "\n".join(lines)

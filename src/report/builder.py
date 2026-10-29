"""报告构建器 — 将分析结果组装为 Markdown 报告"""
import html
import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from data.schemas import AnalysisResult
from report.formatter import (
    _news_text,
    format_hk_news_markdown,
    format_metric_value,
    format_metrics,
    format_sentiment_risk_markdown,
    metric_display_name,
)
from report.peer_comparison import format_peer_comparison_markdown
from report.presentation import compact_quality, compact_score_detail


def build_disclaimer(*, hk: bool = False) -> list[str]:
    """Markdown 与结构化报告共用免责声明，避免两端文案漂移。"""
    paragraphs = [
        "本报告仅供信息参考与研究使用，不构成投资建议或收益保证，不应作为投资决策的唯一依据。",
        "报告基于公开数据生成，数据可能存在延迟、缺失或误差，实际来源与覆盖范围请参阅「数据覆盖与时效」。",
    ]
    if hk:
        paragraphs.append("财务金额按数据源返回值展示；目录列报币种不等同于金额币种，金额币种未核实时不作金额趋势比较，不推断上游是否换汇。港币行情与财报金额不混算。财报按报告期展示，不将半年度或年度报告视作季度数据。")
    return paragraphs + [
        "指标、评分及观察结论受数据口径、样本范围和分析方法限制，不代表对未来表现的预测。",
        "请结合自身风险承受能力独立判断，并核实相关信息。市场有风险，投资需谨慎。",
    ]


_QUALITY_STATUS = {"ok": "正常", "partial": "部分数据", "unavailable": "数据不可用", "not_applicable": "不适用"}


def _md_cell(value) -> str:
    """表内生成值只转义 HTML 与竖线，不把日期等普通字符写成实体码。"""
    return (html.escape(str(value), quote=False)
            .replace("|", "&#124;").replace("\n", " ").replace("\r", " "))


def _quality_range(sample: str, cutoff: str) -> str:
    if not cutoff or cutoff == "未记录":
        return sample
    if sample == "未记录":
        return f"截至 {cutoff}"
    return sample if cutoff in sample else f"{sample}；截至 {cutoff}"


def _quality_table(rows) -> str:
    """由精简字段生成五维覆盖表，状态与范围只在此处展示。"""
    prepared = []
    for row in rows:
        sample = _md_cell(row.get("sample") or "未记录")
        cutoff = str(row.get("cutoff") or "未记录")
        prepared.append({
            "title": _md_cell(row.get("title") or "未记录"),
            "status": _QUALITY_STATUS.get(str(row.get("status")), "未记录"),
            "range": _quality_range(sample, cutoff),
            "source": _md_cell(row.get("source") or "未记录"),
        })
    return _md_table(prepared, {"维度": "title", "状态": "status", "数据范围／截至": "range", "来源": "source"})


def _display_width(s: str) -> int:
    """计算字符串显示宽度，CJK 字符计 2，ASCII 计 1"""
    w = 0
    for ch in s:
        # Unicode 全角范围：CJK、全角标点、全角字母数字
        if (
            '一' <= ch <= '鿿'    # CJK 统一汉字
            or '　' <= ch <= '〿'  # CJK 标点
            or '＀' <= ch <= '￯'  # 全角形式
            or '⺀' <= ch <= '⻿'  # CJK 部首补充
            or '⼀' <= ch <= '⿟'  # 康熙部首
            or '︰' <= ch <= '﹏'  # CJK 兼容形式
        ):
            w += 2
        else:
            w += 1
    return w


def _md_table(data, headers=None):
    """Jinja2 filter：将 dict 或 list[dict] 转为对齐的 Markdown 表格"""
    rows = []

    if isinstance(data, dict):
        data = format_metrics(data)
        # 过滤掉列表和仅供 Web 渲染的内部映射，它们在模板中单独展示。
        filtered = {
            k: v
            for k, v in data.items()
            if not isinstance(v, (list, dict)) and k not in {"peer_names", "peer_display_note", "tag", "roe_trend", "amount_basis", "gross_margin_basis"}
            and not (k == "trend_note" and not v)
        }
        if not filtered:
            return ""
        headers = ["指标", "数值"]
        for k, v in filtered.items():
            rows.append([metric_display_name(k), str(format_metric_value(k, v))])

    elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        if headers is None:
            display_headers = list(data[0].keys())
            data_keys = display_headers
        elif isinstance(headers, dict):
            display_headers = list(headers.keys())
            data_keys = [headers[h] for h in display_headers]
        else:
            display_headers = list(headers)
            data_keys = display_headers
        for item in data:
            row = []
            for key in data_keys:
                val = item.get(key, "")
                if key == "value" and item.get("plain_text"):
                    val = _news_text(val)
                row.append(str(format_metric_value(key, val)))
            rows.append(row)
        headers = display_headers
    else:
        return ""

    if not rows:
        return ""

    # 计算每列最大宽度
    col_widths = [_display_width(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], _display_width(cell))

    def pad_cell(cell: str, width: int) -> str:
        cw = _display_width(cell)
        return cell + " " * (width - cw)

    lines = []
    # 表头
    header_line = "| " + " | ".join(pad_cell(str(h), w) for h, w in zip(headers, col_widths)) + " |"
    lines.append(header_line)
    # 分隔线
    sep_line = "|-" + "-|-".join("-" * w for w in col_widths) + "-|"
    lines.append(sep_line)
    # 数据行
    for row in rows:
        line = "| " + " | ".join(pad_cell(cell, w) for cell, w in zip(row, col_widths)) + " |"
        lines.append(line)

    return "\n".join(lines)


class ReportBuilder:
    def __init__(self):
        template_dir = Path(__file__).parent / "templates"
        self._env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._env.filters["md_table"] = _md_table
        self._env.filters["plain_text"] = _news_text
        self._env.filters["compact_score_detail"] = compact_score_detail
        self._env.filters["quality_table"] = _quality_table
        self._env.filters["hk_news_markdown"] = format_hk_news_markdown
        self._env.filters["sentiment_risk_markdown"] = format_sentiment_risk_markdown
        self._env.filters["peer_comparison_markdown"] = format_peer_comparison_markdown
        self._env.filters["metric_value"] = lambda value, key: format_metric_value(key, value)
        self._env.filters["metric_name"] = metric_display_name
        # 模型仍按原提示词输出编号段落，报告内统一降为第四节的子标题。
        self._env.filters["commentary_section"] = lambda text: re.sub(
            r"^#{1,2}\s+(?=\d+[.、．])", "### ", text, flags=re.MULTILINE)
        from report.formatter import risk_display_name
        self._env.filters["risk_display_name"] = risk_display_name
        self._env.filters["risk_labels"] = lambda flags: list(dict.fromkeys(risk_display_name(flag) for flag in flags))

    def build(self, symbol: str, name: str, results: list[AnalysisResult],
              commentary: dict[str, str], no_llm: bool = False,
              industry: str = "未知", year_high: str = "暂无",
              year_low: str = "暂无", price_position: str = "暂无",
              score_rows: list[dict] | None = None,
              base_score: float | None = 0, risk_deduction: float = 0,
              final_score: float | None = 0, risk_flags: list[str] | None = None,
              market_env: dict | None = None,
              signal: dict | None = None,
              data_quality: dict | None = None,
              price_start: str | None = None, price_end: str | None = None,
              market: str = "A 股", valuation_display: dict | None = None,
              scoring_method: str | None = None) -> str:
        results_map = {r.dimension: r for r in results}
        if market == "港股" and "financial" in results_map:
            financial = results_map["financial"]
            metrics = dict(financial.metrics)
            if "latest_quarter" in metrics:
                metrics["report_period"] = metrics.pop("latest_quarter")
            results_map["financial"] = financial.model_copy(update={"metrics": metrics})
        if data_quality and "summary_rows" not in data_quality:
            data_quality = {**data_quality, **compact_quality(data_quality)}
        template = self._env.get_template("report_v2.jinja2")
        return template.render(
            symbol=symbol,
            name=name,
            market=market,
            scoring_method=scoring_method,
            generated_at=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            no_llm=no_llm,
            industry=industry,
            year_high=year_high,
            year_low=year_low,
            price_position=price_position,
            results_map=results_map,
            score_rows=score_rows or [],
            commentary=commentary,
            base_score=base_score,
            risk_deduction=risk_deduction,
            final_score=final_score,
            risk_flags=risk_flags or [],
            signal=signal,
            data_quality=data_quality,
            price_start=price_start,
            price_end=price_end,
            valuation_display=valuation_display,
            disclaimer=build_disclaimer(hk=market == "港股"),
        )

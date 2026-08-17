"""报告构建器 — 将分析结果组装为 Markdown 报告"""
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from data.schemas import AnalysisResult


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
        # 过滤掉 list 类型字段（如 headlines, peers），它们在表格外处理
        filtered = {k: v for k, v in data.items() if not isinstance(v, list)}
        if not filtered:
            return ""
        headers = ["指标", "数值"]
        for k, v in filtered.items():
            rows.append([str(k), str(v) if v is not None else "N/A"])

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
                row.append(str(val) if val is not None else "N/A")
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

    def build(self, symbol: str, name: str, results: list[AnalysisResult],
              commentary: dict[str, str], no_llm: bool = False,
              industry: str = "未知", year_high: str = "暂无",
              year_low: str = "暂无", price_position: str = "暂无",
              score_rows: list[dict] | None = None,
              base_score: float = 0, risk_deduction: float = 0,
              final_score: float = 0, risk_flags: list[str] | None = None,
              market_env: dict | None = None) -> str:
        results_map = {r.dimension: r for r in results}
        template = self._env.get_template("report_v2.jinja2")
        return template.render(
            symbol=symbol,
            name=name,
            market="A 股",
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
        )

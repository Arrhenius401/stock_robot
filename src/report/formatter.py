"""报告格式化器 — 文件保存和终端渲染"""
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from rich.console import Group
from rich.markdown import Markdown
from rich.table import Table

_SEP_RE = re.compile(r'^\|[-\s:|]+\|$')

METRIC_DISPLAY_NAMES = {
    "latest_quarter": "最新财报季度",
    "revenue": "营业收入",
    "net_profit": "净利润",
    "total_assets": "总资产",
    "total_equity": "股东权益",
    "operating_cash_flow": "经营活动现金流",
    "operating_cash_flow_per_share": "每股经营现金流",
    "revenue_growth_yoy": "营收同比增长",
    "profit_growth_yoy": "净利润同比增长",
    "roe": "ROE",
    "gross_margin": "毛利率",
    "latest_close": "最新收盘价",
    "ma_5": "5 日均线",
    "ma_20": "20 日均线",
    "ma_60": "60 日均线",
    "year_high": "近一年最高价",
    "year_low": "近一年最低价",
    "pe_ttm": "PE(TTM)",
    "pb": "PB",
    "ps_ttm": "PS(TTM)",
    "pe_percentile": "PE 分位",
    "pb_percentile": "PB 分位",
    "dividend_yield": "股息率",
    "valuation_valid": "估值样本有效",
    "percentile_lookback_years": "分位回看年限",
    "sample_start": "样本起始日期",
    "sample_end": "样本结束日期",
    "industry": "所属行业",
    "sector": "所属板块",
    "peer_scope": "同业口径",
    "peer_industry": "可比行业",
    "peer_count": "有效同行数（不含本公司）",
    "industry_median_pe": "行业 PE(TTM) 中位数",
    "industry_median_pb": "行业 PB 中位数",
    "target_pe_premium": "相对行业 PE 溢价",
    "target_market_cap_rank": "行业市值排名",
    "headline_count": "新闻数量",
    "date": "数据日期",
    "north_bound": "北向资金净流入",
    "main_net_inflow": "主力资金净流入",
    "margin_balance": "融资余额",
    "pmi": "PMI",
    "shibor_3m": "3 月期 Shibor",
    "cpi_yoy": "CPI 同比",
    "usd_cny": "美元兑人民币",
    "shibor_percentile": "Shibor 分位",
    "pmi_percentile": "PMI 分位",
    "tag": "信号标签",
}


def metric_display_name(key: object) -> str:
    """返回指标的中文显示名，未知指标保留原键名。"""
    text = str(key)
    return METRIC_DISPLAY_NAMES.get(text, text)


def _is_separator(line: str) -> bool:
    """检测 Markdown 表格分隔行"""
    return bool(_SEP_RE.match(line.strip()))


def _parse_table(lines: list[str]) -> Table:
    """将 Markdown 表格行转为 Rich Table"""
    headers = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    sep_cells = [c.strip() for c in lines[1].strip().strip("|").split("|")]

    table = Table(show_header=True, header_style="bold")
    for header, sep in zip(headers, sep_cells):
        if sep.startswith(":") and sep.endswith(":"):
            justify = "center"
        elif sep.endswith(":"):
            justify = "right"
        else:
            justify = "left"
        table.add_column(header, justify=justify)

    for line in lines[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        table.add_row(*[c if c else "" for c in cells])

    return table


class ReportFormatter:
    @staticmethod
    def save(report: str, symbol: str, output_dir: Path | None = None,
             category: Literal["stock", "index"] = "stock") -> Path:
        if output_dir is None:
            output_dir = Path.cwd() / "reports"
        output_dir = Path(output_dir)
        generated_at = datetime.now().astimezone()
        report_dir = output_dir / category / symbol / generated_at.strftime("%Y-%m")
        report_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{symbol}_{generated_at.strftime('%Y%m%d_%H%M%S')}.md"
        filepath = report_dir / filename
        filepath.write_text(report, encoding="utf-8")
        return filepath

    @staticmethod
    def to_rich_markdown(report: str) -> Markdown | Group:
        """将报告转为 Rich 可渲染对象，表格块用 Table 渲染"""
        lines = report.split("\n")
        renderables = []
        buf: list[str] = []
        i = 0

        while i < len(lines):
            line = lines[i]
            # 检测表格：当前行以 | 开头且下一行是分隔行
            stripped = line.strip()
            if (stripped.startswith("|") and stripped.endswith("|")
                    and i + 1 < len(lines)
                    and _is_separator(lines[i + 1])):
                if buf:
                    renderables.append(Markdown("\n".join(buf)))
                    buf = []

                # 收集表格行
                tbl_lines = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    tbl_lines.append(lines[i])
                    i += 1

                try:
                    renderables.append(_parse_table(tbl_lines))
                except Exception:  # noqa: BLE001 — 表格解析失败回退原始 Markdown
                    renderables.append(Markdown("\n".join(tbl_lines)))
            else:
                buf.append(line)
                i += 1

        if buf:
            renderables.append(Markdown("\n".join(buf)))

        if not renderables:
            return Markdown("")
        if len(renderables) == 1:
            return renderables[0]
        return Group(*renderables)

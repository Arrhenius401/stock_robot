"""报告格式化器 — 文件保存和终端渲染"""
import re
from datetime import datetime
from pathlib import Path
from rich.markdown import Markdown
from rich.table import Table
from rich.console import Group


_SEP_RE = re.compile(r'^\|[-\s:|]+\|$')


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
    def save(report: str, symbol: str, output_dir: Path | None = None) -> Path:
        if output_dir is None:
            output_dir = Path.cwd() / "reports"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        filepath = output_dir / filename
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
                except Exception:
                    # 解析失败时回退为原始 Markdown
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

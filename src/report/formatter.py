"""报告格式化器 — 文件保存和终端渲染"""
from datetime import datetime
from pathlib import Path
from rich.markdown import Markdown


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
    def to_rich_markdown(report: str) -> Markdown:
        return Markdown(report)

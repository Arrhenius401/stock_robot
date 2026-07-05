"""报告构建器 — 将分析结果组装为 Markdown 报告"""
from datetime import datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from src.data.schemas import AnalysisResult


class ReportBuilder:
    def __init__(self):
        template_dir = Path(__file__).parent / "templates"
        self._env = Environment(loader=FileSystemLoader(str(template_dir)))

    def build(self, symbol: str, name: str, results: list[AnalysisResult],
              commentary: dict[str, str]) -> str:
        results_map = {r.dimension: r for r in results}
        template = self._env.get_template("report.jinja2")
        return template.render(
            symbol=symbol,
            name=name,
            market="A 股",
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            results_map=results_map,
            commentary=commentary,
        )

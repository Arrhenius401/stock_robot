"""报告构建器 — 将分析结果组装为 Markdown 报告"""
from datetime import datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from data.schemas import AnalysisResult


class ReportBuilder:
    def __init__(self):
        template_dir = Path(__file__).parent / "templates"
        self._env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def build(self, symbol: str, name: str, results: list[AnalysisResult],
              commentary: dict[str, str], no_llm: bool = False,
              industry: str = "未知", year_high: str = "暂无",
              year_low: str = "暂无", price_position: str = "暂无",
              score_rows: list[dict] | None = None,
              base_score: float = 0, risk_deduction: float = 0,
              final_score: float = 0, risk_flags: list[str] | None = None) -> str:
        results_map = {r.dimension: r for r in results}
        template = self._env.get_template("report_v2.jinja2")
        return template.render(
            symbol=symbol,
            name=name,
            market="A 股",
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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

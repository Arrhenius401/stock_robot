"""IndexReportBuilder — 从 IndexAnalysisContext + list[AnalysisResult] 构建单指数报告"""
from datetime import datetime, date
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from data.schemas import IndexAnalysisContext, AnalysisResult, IndexReport


class IndexReportBuilder:
    def __init__(self, template_dir: Path | None = None):
        if template_dir is None:
            template_dir = Path(__file__).parent.parent / "report" / "templates"
        self._env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        from report.builder import _md_table
        self._env.filters["md_table"] = _md_table

    def build(self, ctx: IndexAnalysisContext,
              results: list[AnalysisResult]) -> IndexReport:
        results_map = {r.dimension: r for r in results}

        tags = self._extract_tags(results_map)
        visible = self._compute_visible_sections(ctx.target.index_style)

        overview = self._build_overview(ctx, results_map)
        technical = self._build_section(results_map, "index_technical")
        valuation = self._build_section(results_map, "index_valuation")
        capital = self._build_section(results_map, "index_capital_flow")
        macro = self._build_section(results_map, "index_macro")
        sentiment = self._build_section(results_map, "index_sentiment")

        comment, coeff = self._composite(tags)

        return IndexReport(
            code=ctx.target.symbol,
            name=ctx.target.name,
            date=date.today(),
            overview=overview,
            section_technical=technical,
            section_valuation=valuation,
            section_capital=capital,
            section_macro=macro if "macro" in visible else None,
            section_sentiment=sentiment,
            tag_technical=tags.get("index_technical", "shake"),
            tag_valuation=tags.get("index_valuation", "invalid"),
            tag_capital=tags.get("index_capital_flow", "neutral"),
            tag_macro=tags.get("index_macro", "na"),
            tag_sentiment=tags.get("index_sentiment", "neutral"),
            composite_comment=comment,
            position_coeff=coeff,
            risk_list=ctx.risk_flags,
            visible_sections=visible,
        )

    def _extract_tags(self, results_map: dict) -> dict:
        tags = {}
        for dim, r in results_map.items():
            tag = r.metrics.get("tag", "") if r.metrics else ""
            if tag:
                tags[dim] = tag
        return tags

    def _compute_visible_sections(self, index_style: str | None) -> set[str]:
        sections = {"overview", "technical", "valuation", "sentiment"}
        if index_style == "broad":
            sections.update({"capital", "macro"})
        elif index_style == "sector":
            sections.add("capital")
        elif index_style == "overseas":
            sections.add("macro")
        return sections

    def _build_overview(self, ctx: IndexAnalysisContext,
                        results_map: dict) -> dict:
        prices = ctx.price_data
        latest_close = prices[-1].close if prices else None
        change_pct = prices[-1].change_pct if prices else None
        val = ctx.valuation_data

        return {
            "latest_close": latest_close,
            "change_pct": change_pct,
            "pe_ttm": val.pe_ttm if val else None,
            "pb": val.pb if val else None,
            "pe_percentile": val.pe_percentile if val else None,
            "valuation_valid": val.valuation_valid if val else True,
            "percentile_lookback_years": val.percentile_lookback_years if val else 5,
        }

    def _build_section(self, results_map: dict, dimension: str) -> dict:
        r = results_map.get(dimension)
        if r is None:
            return {"status": "unavailable", "summary": "", "metrics": {}}
        return {
            "status": r.status,
            "summary": r.summary,
            "metrics": r.metrics,
        }

    def _composite(self, tags: dict) -> tuple[str, float | None]:
        bullish = sum(1 for t in tags.values()
                      if t in ("bull", "undervalued", "positive"))
        bearish = sum(1 for t in tags.values()
                     if t in ("bear", "overvalued", "negative"))
        invalid = sum(1 for t in tags.values() if t in ("invalid", "na"))
        active = len(tags) - invalid if len(tags) > invalid else 1

        net = (bullish - bearish) / max(active, 1)

        if net > 0.3:
            comment = "谨慎看多"
        elif net < -0.3:
            comment = "谨慎看空"
        else:
            comment = "中性震荡"

        coeff = round(max(0.1, min(0.9, 0.5 + net * 0.4)), 2)
        return comment, coeff

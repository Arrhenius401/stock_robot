"""IndexCompareReportBuilder — 多指数横向对比"""
from dataclasses import dataclass, field
from data.schemas import IndexAnalysisContext, IndexReport


@dataclass
class CompareTable:
    """横向对比表格"""
    rows: list[dict] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)


class IndexCompareReportBuilder:
    def build(self, contexts: list[IndexAnalysisContext],
              reports: list[IndexReport]) -> CompareTable:
        headers = [
            "指数名称", "最新点位", "涨跌幅", "PE 分位", "PB 分位",
            "趋势", "估值", "资金", "综合评级",
        ]
        rows = []
        for ctx, report in zip(contexts, reports):
            val = ctx.valuation_data
            change_str = (
                f"{report.overview.get('change_pct', 0):+.2f}%"
                if report.overview.get("change_pct") is not None
                else "N/A"
            )
            rows.append({
                "name": ctx.target.name,
                "latest": report.overview.get("latest_close", "N/A"),
                "change": change_str,
                "pe_pct": f"{val.pe_percentile:.0f}%" if val and val.pe_percentile is not None else "N/A",
                "pb_pct": f"{val.pb_percentile:.0f}%" if val and val.pb_percentile is not None else "N/A",
                "trend": report.tag_technical,
                "valuation": report.tag_valuation,
                "capital": report.tag_capital,
                "composite": report.composite_comment,
            })
        return CompareTable(headers=headers, rows=rows)

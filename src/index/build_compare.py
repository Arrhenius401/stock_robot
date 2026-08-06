"""IndexCompareReportBuilder — 多指数横向对比"""
from dataclasses import dataclass, field
from data.schemas import IndexAnalysisContext, IndexReport, IndexValuationData


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
            # 防御性取值：报告结构不完整（如 mock/异常数据）时降级为 N/A，不中断整个管道
            overview = report.overview if isinstance(report.overview, dict) else {}
            change_pct = overview.get("change_pct", 0)
            change_str = f"{change_pct:+.2f}%" if change_pct is not None else "N/A"
            pe_valid = isinstance(val, IndexValuationData) and val.pe_percentile is not None
            pb_valid = isinstance(val, IndexValuationData) and val.pb_percentile is not None
            rows.append({
                "name": ctx.target.name,
                "latest": overview.get("latest_close", "N/A"),
                "change": change_str,
                "pe_pct": f"{val.pe_percentile:.0f}%" if pe_valid else "N/A",
                "pb_pct": f"{val.pb_percentile:.0f}%" if pb_valid else "N/A",
                "trend": report.tag_technical,
                "valuation": report.tag_valuation,
                "capital": report.tag_capital,
                "composite": report.composite_comment,
            })
        return CompareTable(headers=headers, rows=rows)

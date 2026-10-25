"""IndexCompareReportBuilder — 多指数横向对比"""
from dataclasses import dataclass, field

from data.schemas import IndexAnalysisContext, IndexReport, IndexValuationData
from report.capital_display import capital_presentation
from report.formatter import format_metric_value
from report.valuation_dates import date_presentation, index_date_presentation
from utils.numbers import is_positive_finite, is_valid_percentile


@dataclass
class CompareTable:
    """横向对比表格"""
    rows: list[dict] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    valuation_cutoff: str = ""


class IndexCompareReportBuilder:
    def build(self, contexts: list[IndexAnalysisContext],
              reports: list[IndexReport]) -> CompareTable:
        headers = [
            "指数名称", "最新点位", "涨跌幅", "PE 分位", "PB 分位",
            "趋势", "估值", "资金", "综合评级",
        ]
        rows = []
        views = []
        for ctx in contexts:
            val = ctx.valuation_data
            metrics = {key: getattr(val, key, None) for key in ("pe_percentile", "pb_percentile")}
            views.append(index_date_presentation(val, metrics, strategy=ctx.target.index_style == "strategy"))
        baseline = next((view["baseline"] for view in views if view["baseline"]), None)
        for ctx, report in zip(contexts, reports):
            val = ctx.valuation_data
            # 防御性取值：报告结构不完整（如 mock/异常数据）时降级为 N/A，不中断整个管道
            overview = report.overview if isinstance(report.overview, dict) else {}
            change_pct = overview.get("change_pct", 0)
            change_str = format_metric_value("change_pct", change_pct)
            view = views[len(rows)]
            def percentile_text(value, snapshot, key, val=val, view=view):
                if not isinstance(val, IndexValuationData):
                    return "暂无数据"
                if val.valuation_valid and is_positive_finite(snapshot) and is_valid_percentile(value):
                    return date_presentation({key: value}, view["dates"], baseline=baseline)["metrics"][key]
                return "历史样本不足或指标不适用"
            rows.append({
                "name": ctx.target.name,
                "latest": format_metric_value("latest_close", overview.get("latest_close")),
                "change": change_str,
                "pe_pct": percentile_text(val.pe_percentile if val else None, val.pe_ttm if val else None, "pe_percentile"),
                "pb_pct": percentile_text(val.pb_percentile if val else None, val.pb if val else None, "pb_percentile"),
                "trend": report.tag_technical,
                "valuation": report.tag_valuation,
                "capital": "N/A" if ctx.target.index_style == "strategy" else report.tag_capital,
                "capital_display": {"label": "不适用", "tone": "gray"} if ctx.target.index_style == "strategy"
                else capital_presentation(report.section_capital),
                "composite": report.composite_comment,
            })
        cutoff = date_presentation({}, {}, baseline=baseline)["cutoff"]
        return CompareTable(headers=headers, rows=rows, valuation_cutoff=cutoff)

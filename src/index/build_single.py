"""IndexReportBuilder — 从 IndexAnalysisContext + list[AnalysisResult] 构建单指数报告"""
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from data.schemas import AnalysisResult, IndexAnalysisContext, IndexReport
from index.presentation import build_quality, display_rows, valuation_rows
from report.builder import build_disclaimer
from report.capital_display import capital_presentation
from report.formatter import _news_text, _news_url, format_metric_value
from report.valuation_dates import index_date_presentation
from utils.numbers import is_finite_number, is_positive_finite, is_valid_percentile


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
        self._env.filters["metric_value"] = lambda value, key: format_metric_value(key, value)

    def build(self, ctx: IndexAnalysisContext,
              results: list[AnalysisResult]) -> IndexReport:
        if not ctx.extra_analysis_enabled:
            from analysis.mode import objective_results

            results = objective_results(results)
        results_map = {r.dimension: r for r in results}

        tags = self._extract_tags(results_map)
        val = ctx.valuation_data
        if not val or not val.valuation_valid or not is_positive_finite(val.pe_ttm) or not is_valid_percentile(val.pe_percentile):
            tags["index_valuation"] = "invalid"
        visible = self._compute_visible_sections(ctx.target.index_style)
        if ctx.target.market == "hk":
            visible.update({"capital", "macro", "sentiment"})
            visible.discard("strategy")

        overview = self._build_overview(ctx, results_map)
        technical = self._build_section(results_map, "index_technical")
        valuation = self._build_section(results_map, "index_valuation")
        valuation["display"] = index_date_presentation(val, valuation["metrics"], strategy=ctx.target.index_style == "strategy", summary=valuation["summary"])
        overview["valuation_display"] = index_date_presentation(val, overview, strategy=ctx.target.index_style == "strategy")
        capital = self._build_section(results_map, "index_capital_flow")
        capital["display"] = capital_presentation(capital)
        macro = self._build_section(results_map, "index_macro")
        sentiment = self._build_section(results_map, "index_sentiment")

        performance = self._build_section(results_map, "index_performance")
        strategy = self._build_section(results_map, "index_strategy")
        technical["display"] = {"rows": display_rows(technical["metrics"], ("ma_5", "ma_20", "ma_60", "adx_14", "atr_20", "atr_pct", "year_high", "year_low", "price_position_pct"))}
        valuation["display"]["rows"] = valuation_rows(ctx)
        capital_keys = ("southbound_sh_yi", "southbound_sz_yi") if ctx.target.market == "hk" else ("north_bound", "main_net_inflow", "margin_balance")
        capital["display"] = {**capital["display"], "rows": display_rows(capital["metrics"], capital_keys) if capital["status"] != "unavailable" else []}
        # 资金文字统一使用独立值的展示，避免旧分析摘要把零流量写成净流出。
        if ctx.target.market == "hk":
            capital["display"]["summary"] = capital["summary"] or "港股通市场背景不可用"
        else:
            capital["summary"] = capital["display"]["summary"]
        if capital["status"] != "unavailable":
            notes = capital["metrics"].get("notes") or []
            if notes:
                capital["summary"] += "；" + "；".join(str(note) for note in notes)
        macro["display"] = {"rows": display_rows(macro["metrics"], ("pmi", "shibor_3m", "cpi_yoy", "usd_cny", "hibor_1m", "hk_base_rate", "hk_closing_balance_million"))}
        sentiment["display"] = {"rows": [], "items": sentiment["metrics"].get("items") or sentiment["metrics"].get("news_items") or []}
        performance["display"] = {"rows": [{"label": label, "value": format_metric_value(key, performance["metrics"][key]), "note": ""} for key, label in (("return_pct", "区间收益率"), ("max_drawdown_pct", "最大回撤"), ("annualized_volatility_pct", "年化波动率"), ("benchmark_aligned_index_return_pct", "同日指数区间收益"), ("benchmark_return_pct", "同日基准区间收益"), ("excess_return_pct", "同日基准超额收益")) if performance["metrics"].get(key) is not None], "windows": performance["metrics"].get("windows") or []}
        performance["display"]["etf_rows"] = []
        if performance["metrics"].get("etf_symbol"):
            metrics = performance["metrics"]
            performance["display"]["etf_rows"] = [
                {"label": "ETF代码", "value": str(metrics["etf_symbol"]), "note": "ETF自身表现，不以指数代替"},
                {"label": "ETF区间收益率", "value": format_metric_value("return_pct", metrics.get("etf_return_pct")), "note": str(metrics.get("etf_return_basis") or "口径未记录")},
                {"label": "ETF实际区间", "value": f"{metrics.get('etf_start_date') or '未记录'}至{metrics.get('etf_end_date') or '未记录'}", "note": str(metrics.get("etf_unavailable_reason") or "")},
            ]
        performance["display"]["benchmark_rows"] = [row for row in performance["display"]["rows"] if "同日" in row["label"]]
        strategy_metrics = strategy["metrics"]
        strategy["display"] = {"rows": []}
        for key, label in (("source", "来源"), ("as_of", "采样日"), ("weight_as_of", "官方权重日"), ("financial_years", "财报年度"), ("market_cap_dates", "市值采样日期"), ("member_weight_coverage_pct", "成分权重覆盖率"), ("top10_weight_pct", "前十大集中度"), ("industry_coverage_pct", "行业分类覆盖率")):
            value = strategy_metrics.get(key)
            if value is not None:
                text = "、".join(map(str, value)) if isinstance(value, list) else format_metric_value(key, value)
                strategy["display"]["rows"].append({"label": label, "value": text, "note": ""})
        for indicator in strategy_metrics.get("indicators") or []:
            value = indicator.get("value")
            text = f"{value:.4f}{indicator.get('unit', '')}" if is_finite_number(value) else "不可用"
            strategy["display"]["rows"].append({"label": indicator.get("label", "专项指标"), "value": text, "note": f"官方权重覆盖率 {indicator.get('coverage_pct', '未记录')}%；{indicator.get('scope', '')}；{indicator.get('reason', '')}"})
        for industry in strategy_metrics.get("industry_weights") or []:
            strategy["display"]["rows"].append({"label": industry.get("industry", "未记录行业"), "value": f"{industry.get('weight_pct', '未记录')}%", "note": "占完整指数的原始官方权重"})
        overview["display_rows"] = display_rows(overview, ("latest_close", "change_pct")) + valuation_rows(ctx)
        for row in overview["display_rows"][:2]:
            row["note"] = overview["price_date"]
        sections = {"performance": performance, "technical": technical, "valuation": valuation, "capital": capital, "macro": macro, "sentiment": sentiment, "strategy": strategy}
        quality = build_quality(ctx, sections)
        sample_count = len({p.trade_date for p in ctx.price_data if p.symbol == ctx.target.symbol and p.close > 0})
        comment = "主要行情数据不可用，暂不作整体方向判断。" if not sample_count else "以下仅按可用事实说明，不生成方向评级。"
        commentary = "### 核心判断\n\n" + comment + "\n\n### 关键依据与分歧\n\n" + (technical["summary"] or "行情证据不足。") + "\n\n" + (valuation["summary"] or "估值依据不足。") + "\n\n### 后续观察重点\n\n跟踪短中期均线关系、实际风险区间及估值数据更新；缺失维度不补零，不推断方向。"
        coeff = None
        if not ctx.extra_analysis_enabled:
            commentary = ""
            comment = ""

        return IndexReport(
            analysis_mode="extra" if ctx.extra_analysis_enabled else "objective",
            code=ctx.target.symbol,
            name=ctx.target.name,
            date=datetime.now().astimezone().date(),
            overview=overview,
            section_technical=technical,
            section_valuation=valuation,
            section_capital=capital,
            section_macro=macro if "macro" in visible else None,
            section_sentiment=sentiment,
            section_strategy=strategy if "strategy" in visible else None,
            section_performance=performance,
            requested_instrument=ctx.requested_instrument,
            report_version=2,
            generated_at=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            data_quality=quality, commentary=commentary,
            commentary_status=("not_requested" if not ctx.extra_analysis_enabled else
                               "disabled" if sample_count >= 60 else "insufficient"),
            disclaimer=[p.replace("指标、评分及观察结论", "指标及观察结论") for p in build_disclaimer()],
            tag_technical=tags.get("index_technical", "shake") if ctx.extra_analysis_enabled else None,
            tag_valuation=tags.get("index_valuation", "invalid") if ctx.extra_analysis_enabled else None,
            tag_capital=tags.get("index_capital_flow", "neutral") if ctx.extra_analysis_enabled else None,
            tag_macro=tags.get("index_macro", "na") if ctx.extra_analysis_enabled else None,
            tag_sentiment=tags.get("index_sentiment", "neutral") if ctx.extra_analysis_enabled else None,
            composite_comment=comment,
            position_coeff=coeff,
            # 此处全部为采集与数据覆盖诊断，客观模式也必须保留。
            risk_list=ctx.risk_flags,
            visible_sections=visible,
        )

    def _extract_tags(self, results_map: dict) -> dict:
        tags = {}
        for dim, r in results_map.items():
            tag = r.metrics.get("tag", "") if r.metrics else ""
            if dim == "index_valuation" and (r.status == "unavailable"
                    or not r.metrics.get("valuation_valid", True)
                    or not is_valid_percentile(r.metrics.get("pe_percentile"))):
                tag = "invalid"
            if tag:
                tags[dim] = tag
        return tags

    def _compute_visible_sections(self, index_style: str | None) -> set[str]:
        sections = {"overview", "technical", "valuation", "sentiment", "performance"}
        if index_style == "broad":
            sections.update({"capital", "macro"})
        elif index_style == "sector":
            sections.add("capital")
        elif index_style == "strategy":
            sections.update({"strategy", "performance"})
            sections.discard("sentiment")
        elif index_style == "overseas":
            sections.add("macro")
        return sections

    def _build_overview(self, ctx: IndexAnalysisContext,
                        results_map: dict) -> dict:
        prices = sorted({p.trade_date: p for p in ctx.price_data if p.symbol == ctx.target.symbol and is_positive_finite(p.close)}.values(), key=lambda p: p.trade_date)
        latest_close = prices[-1].close if prices else None
        change_pct = prices[-1].change_pct if prices else None
        val = ctx.valuation_data

        return {
            "price_date": str(prices[-1].trade_date) if prices else "未记录",
            "latest_close": latest_close,
            "change_pct": change_pct,
            "pe_ttm": val.pe_ttm if val and is_finite_number(val.pe_ttm) else None,
            "pb": val.pb if val and is_finite_number(val.pb) else None,
            "pe_snapshot": val.pe_snapshot if val and is_finite_number(val.pe_snapshot) else None,
            "pe_sample_count": val.pe_sample_count if val else 0,
            "strategy_valuation": ctx.target.index_style == "strategy",
            "pe_percentile": val.pe_percentile if val and val.valuation_valid and is_positive_finite(val.pe_ttm) and is_valid_percentile(val.pe_percentile) else None,
            "valuation_valid": bool(val and val.valuation_valid and is_positive_finite(val.pe_ttm) and is_valid_percentile(val.pe_percentile)),
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
        return "不生成整体方向评级", None



def render_index_report_markdown(report: IndexReport) -> str:
    """将指数结构化报告渲染为可保存、可下载的 Markdown。"""
    template_dir = Path(__file__).parent.parent / "report" / "templates"
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    from index.enricher import INDEX_TAG_LABELS
    from report.builder import _md_table, _quality_table
    env.filters["news_text"] = _news_text
    env.filters["news_url"] = _news_url
    env.filters["quality_table"] = _quality_table
    env.filters["md_table"] = _md_table
    env.filters["metric_value"] = lambda value, key: format_metric_value(key, value)
    template = env.get_template("index_report.jinja2")
    return template.render(
        code=report.code,
        name=report.name,
        generated_at=report.generated_at or str(report.date),
        report=report,
        overview=report.overview,
        section_technical=report.section_technical,
        section_valuation=report.section_valuation,
        section_capital=report.section_capital,
        capital_display=capital_presentation(report.section_capital),
        section_macro=report.section_macro,
        section_sentiment=report.section_sentiment,
        section_strategy=getattr(report, "section_strategy", None),
        section_performance=getattr(report, "section_performance", None),
        requested_instrument=getattr(report, "requested_instrument", None),
        tag_technical=report.tag_technical,
        tag_valuation=report.tag_valuation,
        tag_capital=report.tag_capital,
        tag_macro=report.tag_macro,
        tag_sentiment=report.tag_sentiment,
        composite_comment=report.composite_comment,
        position_coeff=report.position_coeff,
        visible_sections=report.visible_sections,
        tag_labels=INDEX_TAG_LABELS,
    )

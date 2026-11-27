"""报告数据覆盖、来源和复核事项的共享事实视图。"""
from datetime import date, datetime
from typing import Any

from analysis.valuation_rules import valuation_snapshot_dates
from data.schemas import AnalysisContext, AnalysisResult, DataReference
from data.validation import (
    FINANCIAL_ANALYSIS_FIELDS,
    financial_has_facts,
    has_finite_facts,
)
from report.capability import dimension_state
from report.formatter import format_metric_value
from report.market_profile import MarketProfile, profile_for
from report.presentation import assessment_fields
from report.scoring import DIM_WEIGHTS, participation_score
from utils.numbers import is_finite_number

_DIMENSIONS = (
    ("financial", "财务", "financial", "financial", DIM_WEIGHTS["financial"]),
    ("technical", "技术", "price", "price", DIM_WEIGHTS["technical"]),
    ("valuation", "估值", "valuation", "valuation", DIM_WEIGHTS["valuation"]),
    ("industry", "行业", "industry", "industry", DIM_WEIGHTS["industry"]),
    ("sentiment", "舆情", "sentiment", "news", DIM_WEIGHTS["sentiment"]),
)
_LEVEL = {"sufficient": 2, "partial": 1, "insufficient": 0}
_STATUS = {2: "ok", 1: "partial", 0: "unavailable"}
_RESULT_LEVEL = {"ok": 2, "partial": 1, "unavailable": 0}
_HK_METRICS = {
    "financial": FINANCIAL_ANALYSIS_FIELDS,
    "technical": {"latest_close", "ma_5", "ma_20", "ma_60", "volume_ratio", "macd_dif", "macd_dea", "macd_bar"},
    "valuation": {"pe_ttm", "pb", "ps_ttm"},
    "industry": {"peer_count", "industry_median_pe", "industry_median_pb"},
}


def _has_hk_facts(result: AnalysisResult) -> bool:
    return result.status in ("ok", "partial") and has_finite_facts(
        result.metrics, _HK_METRICS.get(result.dimension, set()))


def hk_industry_label(ctx: AnalysisContext, results: list[AnalysisResult]) -> str:
    """港股概览行业标签；区分可比较、非港股通、待补齐和数据不可用。"""
    if ctx.industry_data is not None and ctx.industry_data.industry:
        return f"恒生HSICS：{ctx.industry_data.industry}"
    industry = next((result for result in results if result.dimension == "industry"), None)
    summary = industry.summary if industry is not None else ""
    if "非港股通" in summary:
        return "非港股通（不支持行业对比）"
    if "待补齐" in summary:
        return "恒生HSICS行业待补齐"
    return "恒生HSICS行业数据不可用"


def has_analysis_data(results: list[AnalysisResult], ctx: AnalysisContext | None = None) -> bool:
    """有效核心分或实测事实可用于解读，交易信号另行检查充分度。"""
    core = [result for result in results if result.dimension in DIM_WEIGHTS and result.dimension != "sentiment"]
    if any(participation_score(result) is not None for result in core):
        return True
    return any(_has_hk_facts(result) for result in core)


def has_usable_facts(ctx: AnalysisContext) -> bool:
    """原始事实独立于分析器是否能评分，空模型不视为可用事实。"""
    quote = ctx.current_quote
    return bool(ctx.price_data or (
        quote.get("status") == "ok" and is_finite_number(quote.get("price")) and quote["price"] > 0)
        or financial_has_facts(ctx.financial_data or [], ctx.symbol)
        or (ctx.valuation_data and has_finite_facts(ctx.valuation_data.model_dump(), {"pe_ttm", "pb", "ps_ttm"}))
        or (ctx.industry_data and ctx.industry_data.industry.strip() not in {"", "未知"}))


def _sample_dates(ctx: AnalysisContext, key: str) -> tuple[str, str]:
    """只描述实际持有的样本；不使用报告生成时间代替数据截止。"""
    dates: list[date] = []
    sample = "未记录"
    if key == "financial":
        dates = [p.fiscal_quarter for p in ctx.financial_data or []]
        if dates:
            sample = f"{len(dates)}期财报"
    elif key == "price":
        dates = [p.trade_date for p in ctx.price_data or []]
        if dates:
            sample = f"{len(dates)}个交易日（{min(dates).isoformat()} 至 {max(dates).isoformat()}）"
    elif key == "valuation":
        dates = [p.trade_date for p in ctx.enriched_valuation.daily_points if p.pe is not None or p.pb is not None] if ctx.enriched_valuation else []
        if dates:
            sample = f"{len(dates)}个估值样本（{min(dates).isoformat()} 至 {max(dates).isoformat()}）"
        elif ctx.valuation_data:
            periods = valuation_snapshot_dates(ctx)
            known = {period for period in periods.values() if period is not None}
            sample = "分项估值快照" if len(known) > 1 else "单日估值快照" if known else "估值快照（日期未记录）"
        if ctx.valuation_data:
            dates.extend(period for period in valuation_snapshot_dates(ctx).values() if period is not None)
    elif key == "industry":
        count = ctx.enriched_industry.peer_count if ctx.enriched_industry else len(ctx.industry_data.peers) if ctx.industry_data else 0
        if count:
            sample = f"{count}家可比公司"
        comparison = ctx.enriched_industry.comparison if ctx.enriched_industry else {}
        data_date = comparison.get("valuation", {}).get("as_of") or (
            getattr(ctx.industry_data, "_data_date", "") if ctx.industry_data else "")
        if data_date:
            try:
                dates.append(datetime.fromisoformat(str(data_date)).date())
            except (ValueError, TypeError):
                dates = []
    elif key == "news":
        if ctx.raw_sentiment:
            dates = [p.publish_date for p in ctx.raw_sentiment.items]
            sample = f"{len(ctx.raw_sentiment.items)}条新闻与公告"
        elif ctx.news_data and ctx.news_data.hk_news:
            dates = [item.published_at.date() for item in ctx.news_data.hk_news.items]
            sample = f"{len(dates)}条候选新闻"
        elif ctx.news_data:
            sample = f"{len(ctx.news_data.headlines)}条新闻"
            # NewsData.date 是抓取日期，不能代替消息发布日期。
    return sample, max(dates).isoformat() if dates else "未记录"


def _reference_fields(reference: DataReference) -> list[dict[str, Any]]:
    """仅构建历史参考的显示字段，不修改当前事实、充分度或计分结果。"""
    note = "以下为此前取得的数据，仅供历史参考，不进入本次综合评分或操作信号"
    if reference.reason:
        note = f"{reference.reason}；{note}"
    values = [
        ("历史参考（不计分）", note),
        ("参考原采集时间", reference.record.collected_at.isoformat() if reference.record.collected_at else "未记录"),
        ("参考数据来源", reference.record.source or "未记录"),
    ]
    if reference.data_type == "financial" and reference.financial_data:
        latest = max(reference.financial_data, key=lambda row: row.fiscal_quarter)
        values.append(("参考报告期", latest.fiscal_quarter.isoformat()))
        facts = [
            f"{label} {format_metric_value(key, getattr(latest, key))}"
            for key, label in (("revenue", "营业收入"), ("net_profit", "净利润"),
                               ("total_assets", "总资产"), ("total_equity", "净资产"),
                               ("operating_cash_flow", "经营现金流"))
            if is_finite_number(getattr(latest, key))
        ]
        if facts:
            currency = latest.report_currency or "未记录，不推断换汇"
            values.append(("参考财务指标", "；".join(facts) + f"；金额币种：{currency}"))
    elif reference.data_type == "price" and reference.price_data:
        rows = reference.price_data
        latest = max(rows, key=lambda row: row.trade_date)
        first = min(row.trade_date for row in rows)
        values.extend([
            ("参考交易区间", f"{len(rows)}条日线（{first.isoformat()} 至 {latest.trade_date.isoformat()}）"),
            ("参考末收盘价", str(format_metric_value("latest_close", latest.close))),
        ])
    elif reference.data_type == "news" and reference.news_data:
        news = reference.news_data
        raw = news._raw_sentiment
        messages = ([(item.publish_date, item.title) for item in
                     sorted(raw.items, key=lambda item: item.publish_date, reverse=True)] if raw is not None
                    else [(None, title) for title in news.headlines])
        periods = [period for period, _title in messages if period is not None]
        values.extend([
            ("参考消息日期", max(periods).isoformat() if periods else "未记录"),
            ("参考消息数量", f"{len(messages)}条（最多展示5条标题）"),
        ])
        values.extend((f"参考消息 {index}", f"{period.isoformat() if period else '发布日期未记录'} · {title}")
                      for index, (period, title) in enumerate(messages[:5], 1))
    # 网页继续显示原文；仅 Markdown 表格针对这些外部纯文本做转义。
    return [{"label": label, "value": value, "plain_text": True, "reference_only": True} for label, value in values]


def dedupe_pending(items: list[str]) -> list[str]:
    """去除重复核验原因与政策说明。"""
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if "美股舆情" in item and "未采集" in item:
            continue
        clauses = []
        for clause in str(item).split("；"):
            clause = clause.strip()
            if any(term in clause for term in ("资本开支", "自由现金流", "FCF", "市值排名")):
                continue
            if clause and clause not in seen:
                seen.add(clause)
                clauses.append(clause)
        if clauses:
            result.append("；".join(clauses))
    return result


def build_data_quality(ctx: AnalysisContext, results: list[AnalysisResult], *,
                       include_references: bool = True, profile: MarketProfile | None = None) -> dict[str, Any]:
    """五维按原权重表达数据覆盖，A 股交易信号保持原三核心门槛。"""
    profile = profile or profile_for(ctx.market)
    result_map = {r.dimension: r for r in results}
    records = getattr(ctx, "collection_records", {})
    dimensions: list[dict[str, Any]] = []
    pending: list[str] = []
    coverage = 0.0
    has_scored = False
    all_complete = True
    signal_complete = True
    signal_blocked: list[str] = []
    hk = ctx.market == "hk"
    us = ctx.market == "us"
    applicable = set(DIM_WEIGHTS)
    applicable_weight = sum(DIM_WEIGHTS[key] for key in applicable) or 1.0
    available = sum(participation_score(result_map.get(key)) is not None for key in DIM_WEIGHTS)
    for key, title, suff_key, source_key, weight in _DIMENSIONS:
        pending_start = len(pending)
        suff = getattr(ctx.sufficiency, suff_key)
        result = result_map.get(key)
        level = min(_LEVEL.get(suff.level, 0), _RESULT_LEVEL.get(result.status, 0) if result else 0)
        value = participation_score(result)
        # 新财务依据已逐子项核验，不能被港股旧四季度计数规则永久降级。
        if key == "financial" and result and result.metrics.get("scoring_basis") and value is not None:
            level = _RESULT_LEVEL.get(result.status, 0)
        if value is None and ctx.extra_analysis_enabled:
            level = min(level, 1)
            if hk and (result is None or not _has_hk_facts(result)):
                level = 0
        if not ctx.extra_analysis_enabled:
            facts = bool(result and (_has_hk_facts(result) or (
                key == "sentiment" and result.status != "unavailable" and
                (result.metrics.get("news_items") or result.metrics.get("headlines")))))
            if not facts:
                level = 0
            elif key == "sentiment" and result is not None:
                # 原新闻充足度等待模型评估；客观模式直接采用采集状态。
                level = _RESULT_LEVEL.get(result.status, 0)
                news_record = records.get(source_key)
                if getattr(news_record, "error_message", "") or getattr(news_record, "cache_status", "") == "stale":
                    level = min(level, 1)
        if weight and key in applicable:
            coverage += weight * (level / 2)
            if (key in {"financial", "technical", "valuation"} or value is not None
                    or (not ctx.extra_analysis_enabled and key != "sentiment")):
                all_complete = all_complete and level == 2 and (value is not None or not ctx.extra_analysis_enabled)
        if key in {"financial", "technical", "valuation"}:
            sufficient = suff.level == "sufficient"
            if key == "financial" and result and result.metrics.get("scoring_basis"):
                basis = result.metrics["scoring_basis"]
                available_max, eligible_max = basis.get("available_max"), basis.get("eligible_max")
                # 港股原季度计数固定为partial；信号改用已逐项核验的财务证据。
                sufficient = (is_finite_number(available_max) and is_finite_number(eligible_max)
                              and available_max >= eligible_max > 0)
            signal_ready = (sufficient and result is not None
                            and result.status == "ok" and value is not None)
            signal_complete = signal_complete and signal_ready
            if not signal_ready:
                signal_blocked.append(title)
        record = records.get(source_key)
        sample, cutoff = _sample_dates(ctx, source_key)
        if cutoff == "未记录":
            pending.append(f"{title}：数据截止未记录，需核对时效")
        if sample == "未记录" and suff.sample_count:
            sample = f"{suff.sample_count}个样本"
        fields: list[dict[str, Any]] = [
            {"label": "样本范围", "value": sample},
            {"label": "数据来源", "value": getattr(record, "source", "") or "未记录"},
            {"label": "数据截止", "value": cutoff},
        ]
        if key == "valuation" and ctx.valuation_data and (ctx.market in {"hk", "us"} or ctx.valuation_data.history_source.startswith(("东财", "百度"))):
            valuation = ctx.valuation_data
            fields.append({"label": "分位窗口", "value": valuation.history_window_label})
            snapshot_dates = valuation_snapshot_dates(ctx)
            source = getattr(record, "source", "") or valuation._data_source or "未记录"
            fields.append({"label": "估值字段来源", "value": "；".join(filter(None, [
                f"PE(TTM) 快照：{valuation.pe_source or source}（{snapshot_dates['pe_ttm'] or '未记录'}）" if valuation.pe_ttm is not None else "PE(TTM) 快照：未取得",
                f"PB 快照：{valuation.pb_source or source}（{snapshot_dates['pb'] or '未记录'}）" if valuation.pb is not None else "PB 快照：未取得",
                f"历史分位：{valuation.history_source}，各指标日期与样本数见估值说明" if valuation.history_source else "",
                f"PS(TTM)：{valuation.ps_source or '未记录'}（{valuation.ps_as_of or '未记录'}）" if valuation.ps_ttm is not None else "",
            ]))})
        if key == "financial" and ctx.market in {"a-shares", "hk", "us"} and ctx.financial_data:
            latest = max(ctx.financial_data, key=lambda row: row.fiscal_quarter)
            if not latest.field_positions.get("net_profit", {}).get("accounting"):
                fields.append({"label": "财务证据限制", "value": "会计准则未记录；双方为空仅允许现金流比较，不表示已核实准则。"})
            if latest.financial_source and not getattr(record, "source", ""):
                fields[1]["value"] = latest.financial_source
            for name, label in (("capital_expenditure", "资本开支"), ("free_cash_flow", "自由现金流")):
                from data.financial_ttm import financial_ttm

                value = (result.metrics[name] if result is not None and name in result.metrics
                         else financial_ttm(ctx.financial_data, name, ctx.market, require_precision=True))
                fields.append({"label": label + "（TTM，不改变覆盖率）", "value": str(format_metric_value(name, value))})
            if result:
                for name, label in (("capital_expenditure_latest", "最近完整年度资本开支（非 TTM）"),
                                    ("free_cash_flow_latest", "最近完整年度自由现金流（非 TTM）")):
                    if result.metrics.get(name) is not None:
                        fields.append({"label": label, "value": str(format_metric_value(name, result.metrics[name]))})
                for name, label in (("cash_flow_period", "现金流期间"),
                                    ("cash_flow_latest_period", "最近完整年度现金流期间"),
                                    ("cash_flow_limitation", "分析限制")):
                    if result.metrics.get(name):
                        fields.append({"label": label, "value": str(result.metrics[name])})
            if latest.financial_selection_note:
                fields.append({"label": "财务包说明", "value": latest.financial_selection_note})
            if (len(set(latest.field_sources.values())) > 1
                    or any(source != latest.financial_source for source in latest.field_sources.values())):
                from report.formatter import metric_display_name

                # 仅展开最新报告的补位来源，历史逐项记录留在缓存，避免报告无限膨胀。
                sources: dict[str, list[str]] = {}
                for name, source in sorted(latest.field_sources.items()):
                    sources.setdefault(source, []).append(metric_display_name(name))
                fields.append({"label": "最新报告字段来源", "value": "；".join(
                    f"{source}：{'、'.join(names)}" for source, names in sources.items())})
        if (key == "valuation" and ctx.enriched_valuation and ctx.enriched_valuation.daily_points
                and ctx.market not in {"hk", "us"}):
            fields[1]["value"] = "行情与财务派生计算"
            price_source = getattr(records.get("price"), "source", "") or "未记录"
            financial_source = getattr(records.get("financial"), "source", "") or "未记录"
            anchor_source = getattr(record, "source", "") or "未记录"
            fields.append({"label": "完整来源", "value": f"行情：{price_source}；财务：{financial_source}；实测估值快照：{anchor_source}"})
            fields.append({"label": "依赖说明", "value": "估值序列由行情和财务派生计算；实测快照用于锚定"})
        cache_status = getattr(record, "cache_status", "unknown")
        cache_text = {"live": "本次采集", "cache": "缓存", "stale": "过期缓存", "unknown": "未记录"}.get(cache_status, "未记录")
        collected = getattr(record, "collected_at", None)
        if collected:
            cache_text += f" · 原采集时间 {collected.isoformat()}"
        if (key == "valuation" and ctx.enriched_valuation and ctx.enriched_valuation.daily_points
                and ctx.market not in {"hk", "us"}):
            cache_text = f"实测估值快照：{cache_text}；行情与财务缓存状态见对应维度"
        fields.append({"label": "缓存状态", "value": cache_text})
        if key == "financial" and result:
            annual = result.metrics.get("annual_roe") or ctx.financial_scoring_facts.get("annual_roe", {})
            if isinstance(annual, dict) and annual.get("period_end"):
                fields.append({"label": "ROE计分期间", "value": f"{annual.get('period_start') or '未记录'} 至 {annual['period_end']}"})
                fields.append({"label": "ROE计分来源", "value": annual.get("source") or "未记录"})
        if key == "sentiment" and result and ctx.extra_analysis_enabled and not us:
            fields.append({"label": "有效评估", "value": f"{result.metrics.get('evaluated_count', 0)}条相关消息"})
            fields.append({"label": "评估窗口", "value": f"{result.metrics.get('window_start') or '未记录'} 至 {result.metrics.get('window_end') or '未记录'}"})
            if result.metrics.get("assessment_reason"):
                fields.append({"label": "评估说明", "value": str(result.metrics["assessment_reason"])})
        if key == "industry" and profile.annual_industry_policy == "reference_only":
            comparison = ctx.enriched_industry.comparison if ctx.enriched_industry else {}
            annual = comparison.get("periods", {}).get("annual", {})
            if annual.get("reference_only"):
                fields.append({"label": "分析限制", "value": "完整年度仅供参考，行业评分仍采用最新 TTM。"})
        if key == "financial" and profile.financial_special_policy == "facts_only" and ctx.financial_special:
            fields.append({"label": "分析限制", "value": "银行专项为东财 F10 BMAIN 年度披露事实，仅作展示，不参与财务评分或信号。"})
        reasons = [suff.reason] if suff.reason and level < 2 else []
        if key == "sentiment" and not ctx.extra_analysis_enabled and suff.reason == "待充实":
            reasons = []
        if result and result.status != "ok" and result.summary:
            reasons.append(result.summary)
        error = getattr(record, "error_message", "")
        if error:
            reasons.append(error)
            if level == 2:
                # 备用数据充足不代表主源没有失败；报告与 AI 必须保留切换依据。
                fields.append({"label": "采集限制", "value": error})
                pending.append(f"{title}：核对采集限制：{error}")
        if level < 2:
            reason = "；".join(dict.fromkeys(reasons)) or "分析依据或计算结果不足"
            fields.append({"label": "缺失原因" if level == 0 else "分析限制", "value": reason})
            pending.append(f"{title}：核对{reason}")
        if cache_status == "stale":
            pending.append(f"{title}：核对过期缓存是否仍适用于当前分析")
        if key == "valuation" and ctx.enriched_valuation and not ctx.enriched_valuation.validated:
            fields.append({"label": "分析限制", "value": "历史估值序列未经实测锚定校验"})
            pending.append("估值：核对历史估值序列的锚定口径")
        reference = ctx.reference_data.get(source_key) if include_references else None
        if reference is not None and reference.data_type == source_key:
            fields.extend(_reference_fields(reference))
        display_status = _STATUS[level]
        state = dimension_state(profile, result, ctx, dimension=key)
        if us and not state.applicable:
            display_status = state.status
            del pending[pending_start:]
            fields = [field for field in fields if field["label"] not in {"缺失原因", "分析限制"}]
            fields.append({"label": "评估说明", "value": state.detail})
        if profile.business_profile != "ths" and ctx.commentary_version == 2:
            state = dimension_state(profile, result, ctx, dimension=key)
            if not state.applicable:
                display_status = state.status
            elif level == 0 and state.reason in {"collection_failed", "insufficient_sample"}:
                display_status = state.reason
        dimensions.append({"key": key, "title": title, "status": display_status, "fields": fields})
    has_scored = any(participation_score(result_map.get(key)) is not None
                     for key in DIM_WEIGHTS if key != "sentiment")
    analysis_available = has_analysis_data(results, ctx)
    quality = {
        "status": "ok" if all_complete else "partial" if analysis_available else "unavailable",
        "coverage": round(coverage / applicable_weight * 100, 1),
        "signal_allowed": ctx.extra_analysis_enabled and signal_complete and has_scored,
        "signal_unavailable_reason": ("未启用额外分析。" if not ctx.extra_analysis_enabled else
            f"{'、'.join(signal_blocked)}分析依据未满足信号生成条件。" if signal_blocked else ""),
        "has_scored_data": ctx.extra_analysis_enabled and has_scored,
        "has_analysis_data": analysis_available,
        "dimensions": dimensions,
        "pending_verifications": dedupe_pending(pending),
        "coverage_basis": profile.coverage_basis(ctx.extra_analysis_enabled),
    }
    if ctx.market in {"hk", "us"}:
        quality.update(available_dimensions=available, applicable_dimensions=len(applicable))
    if ctx.market in {"a-shares", "hk", "us"}:
        required = ["financial", "technical", "valuation"]
        scored = [key for key in required if participation_score(result_map.get(key)) is not None]
        identity = ctx.identity_status or (
            "confirmed" if ctx.name.strip() and ctx.name.upper() != ctx.symbol.upper()
            else "probable" if ctx.price_data or ctx.current_quote.get("status") == "ok" else "unconfirmed")
        bars = len(ctx.price_data or [])
        core_complete = bars > 0 and len(scored) == len(required) and signal_complete
        complete = core_complete and all_complete
        partial_available = ctx.extra_analysis_enabled and bool(scored)
        assessment_view = assessment_fields(complete=complete, available=partial_available)
        assessment = assessment_view["assessment_status"]
        quality.update(
            required_dimensions=required, scored_required_dimensions=scored,
            identity_status=identity, **assessment_view,
            has_scored_data=quality["has_scored_data"] and assessment != "facts_only",
            signal_allowed=quality["signal_allowed"] and core_complete,
        )
        for dimension in dimensions:
            if dimension["key"] == "sentiment" and profile.sentiment_policy == "not_collected":
                dimension["fields"] = [item for item in dimension["fields"]
                                       if item["label"] not in {"采集限制", "缺失原因", "分析限制"}]
                news = result_map.get("sentiment")
                items = news.metrics.get("news_items") if news else None
                reason = ("美股舆情仅展示新闻事实，不参与评分" if items
                          else "本次无可展示新闻样本；美股舆情仅作事实展示，不参与评分")
                failure = getattr(records.get("news"), "error_message", "")
                if failure:
                    reason += "；" + failure
                dimension["fields"].append({"label": "评估说明", "value": reason})
    from report.presentation import compact_quality

    quality.update(compact_quality(quality))
    return quality

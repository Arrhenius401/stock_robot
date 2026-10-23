"""报告数据覆盖、来源和复核事项的共享事实视图。"""
from datetime import date
from typing import Any

from data.schemas import AnalysisContext, AnalysisResult
from data.validation import FINANCIAL_ANALYSIS_FIELDS, has_finite_facts

_DIMENSIONS = (
    ("financial", "财务", "financial", "financial", 0.30),
    ("technical", "技术", "price", "price", 0.20),
    ("valuation", "估值", "valuation", "valuation", 0.25),
    ("industry", "行业", "industry", "industry", 0.25),
    ("sentiment", "舆情", "sentiment", "news", 0.0),
)
_LEVEL = {"sufficient": 2, "partial": 1, "insufficient": 0}
_STATUS = {2: "ok", 1: "partial", 0: "unavailable"}
_RESULT_LEVEL = {"ok": 2, "partial": 1, "unavailable": 0}
_HK_METRICS = {
    "financial": FINANCIAL_ANALYSIS_FIELDS,
    "technical": {"latest_close", "ma_5", "ma_20", "ma_60", "volume_ratio", "macd_dif", "macd_dea", "macd_bar"},
    "valuation": {"pe_ttm", "pb", "ps_ttm"},
}


def _has_hk_facts(result: AnalysisResult) -> bool:
    return result.status in ("ok", "partial") and has_finite_facts(
        result.metrics, _HK_METRICS.get(result.dimension, set()))


def has_analysis_data(results: list[AnalysisResult], ctx: AnalysisContext | None = None) -> bool:
    """港股使用有效实测指标，A 股维持既有可评分门槛。"""
    if ctx is None or ctx.market != "hk":
        return any(r.dimension in ("financial", "technical", "valuation", "industry")
                   and r.score is not None and r.status != "unavailable" for r in results)
    return any(_has_hk_facts(result) for result in results)


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
            sample = "单日估值快照"
        if ctx.valuation_data:
            val = ctx.valuation_data
            periods = [val.pe_as_of, val.pb_as_of, val.ps_as_of]
            if ctx.market == "hk":
                periods.append(val.date)
            dates.extend(period for period in periods if period is not None)
    elif key == "industry":
        count = ctx.enriched_industry.peer_count if ctx.enriched_industry else len(ctx.industry_data.peers) if ctx.industry_data else 0
        if count:
            sample = f"{count}家可比公司"
    elif key == "news":
        if ctx.raw_sentiment:
            dates = [p.publish_date for p in ctx.raw_sentiment.items]
            sample = f"{len(ctx.raw_sentiment.items)}条新闻与公告"
        elif ctx.news_data:
            sample = f"{len(ctx.news_data.headlines)}条新闻"
            # NewsData.date 是抓取日期，不能代替消息发布日期。
    return sample, max(dates).isoformat() if dates else "未记录"


def build_data_quality(ctx: AnalysisContext, results: list[AnalysisResult]) -> dict[str, Any]:
    """A 股计分覆盖与港股适用维度覆盖分离，字段完整性独立表达。"""
    result_map = {r.dimension: r for r in results}
    records = getattr(ctx, "collection_records", {})
    dimensions: list[dict[str, Any]] = []
    pending: list[str] = []
    coverage = 0.0
    has_scored = False
    all_complete = True
    hk = ctx.market == "hk"
    available = len({result.dimension for result in results if _has_hk_facts(result)}) if hk else 0
    for key, title, suff_key, source_key, weight in _DIMENSIONS:
        if hk and key not in _HK_METRICS:
            dimensions.append({"key": key, "title": title, "status": "not_applicable", "applicable": False,
                               "fields": [{"label": "适用范围", "value": "本期港股分析不支持此维度，不计入覆盖率"}]})
            continue
        suff = getattr(ctx.sufficiency, suff_key)
        result = result_map.get(key)
        level = min(_LEVEL.get(suff.level, 0), _RESULT_LEVEL.get(result.status, 0) if result else 0)
        if hk and (result is None or not _has_hk_facts(result)):
            level = 0
        if ctx.market != "hk" and weight and (result is None or result.score is None):
            level = min(level, 1)
        if weight:
            coverage += weight * (level / 2)
            all_complete = all_complete and level == 2
            has_scored = has_scored or bool(level and result and result.score is not None)
        record = records.get(source_key)
        sample, cutoff = _sample_dates(ctx, source_key)
        if cutoff == "未记录":
            pending.append(f"{title}：数据截止未记录，需核对时效")
        if sample == "未记录" and suff.sample_count:
            sample = f"{suff.sample_count}个样本"
        fields = [
            {"label": "样本范围", "value": sample},
            {"label": "数据来源", "value": getattr(record, "source", "") or "未记录"},
            {"label": "数据截止", "value": cutoff},
        ]
        if key == "valuation" and hk and ctx.valuation_data:
            valuation = ctx.valuation_data
            fields.append({"label": "估值字段来源", "value": "；".join(filter(None, [
                f"PE/PB 快照：{getattr(record, 'source', '') or '未记录'}（{valuation.date}）",
                f"历史分位：{valuation.history_source}，各指标日期与样本数见估值说明" if valuation.history_source else "",
                f"PS(TTM)：{valuation.ps_source}（{valuation.ps_as_of}）" if valuation.ps_ttm is not None else "",
            ]))})
        if key == "financial" and ctx.market in {"a-shares", "hk"} and ctx.financial_data:
            latest = max(ctx.financial_data, key=lambda row: row.fiscal_quarter)
            if latest.financial_source and not getattr(record, "source", ""):
                fields[1]["value"] = latest.financial_source
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
        if key == "valuation" and ctx.enriched_valuation and ctx.enriched_valuation.daily_points and not hk:
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
        if key == "valuation" and ctx.enriched_valuation and ctx.enriched_valuation.daily_points and not hk:
            cache_text = f"实测估值快照：{cache_text}；行情与财务缓存状态见对应维度"
        fields.append({"label": "缓存状态", "value": cache_text})
        reasons = [suff.reason] if suff.reason and level < 2 else []
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
        dimensions.append({"key": key, "title": title, "status": _STATUS[level], "fields": fields})
    analysis_available = has_analysis_data(results, ctx) if hk else has_scored
    quality = {
        "status": "ok" if all_complete else "partial" if analysis_available else "unavailable",
        "coverage": round(available / len(_HK_METRICS) * 100, 1) if hk else round(coverage * 100, 1),
        "signal_allowed": ctx.market != "hk" and all_complete and has_scored,
        "has_scored_data": False if hk else has_scored,
        "has_analysis_data": analysis_available,
        "dimensions": dimensions,
        "pending_verifications": list(dict.fromkeys(pending)),
    }
    if hk:
        quality.update(available_dimensions=available, applicable_dimensions=len(_HK_METRICS),
                       coverage_basis="可用维度覆盖，不表示字段完整率或投资评分")
    return quality

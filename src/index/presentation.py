"""指数报告展示副本与个股共用的数据质量汇总。"""
from typing import Any

from data.schemas import IndexAnalysisContext
from report.formatter import format_metric_value
from report.presentation import compact_quality
from utils.numbers import is_finite_number, is_positive_finite, is_valid_percentile

LABELS = {
    "latest_close": "最新点位", "change_pct": "日涨跌幅", "ma_5": "5日均线", "ma_20": "20日均线", "ma_60": "60日均线",
    "adx_14": "ADX(14)", "atr_20": "ATR(20)（点）", "atr_pct": "ATR占收盘价", "price_position_pct": "近12个月价格位置",
    "year_high": "近12个月最高价", "year_low": "近12个月最低价", "pmi": "PMI", "shibor_3m": "Shibor3M",
    "cpi_yoy": "CPI同比", "usd_cny": "USD/CNY", "north_bound": "北向资金（亿元）", "main_net_inflow": "主力资金（亿元）", "margin_balance": "融资余额",
    "hibor_1m": "HIBOR 1个月（%）", "hk_base_rate": "香港基本利率（%）", "hk_closing_balance_million": "香港总结余（百万港元）",
    "southbound_sh_yi": "港股通（沪）成交净买额（亿元）", "southbound_sz_yi": "港股通（深）成交净买额（亿元）",
}


def display_rows(metrics: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"label": LABELS.get(key, key), "value": f"{metrics[key]:.2f}%" if key in {"atr_pct", "price_position_pct"} else f"{metrics[key]:.2f}" if key in {"adx_14", "atr_20"} else format_metric_value(key, metrics[key]), "note": ""}
            for key in keys if metrics.get(key) is not None]


def build_quality(ctx: IndexAnalysisContext, sections: dict[str, dict]) -> dict:
    dimensions = []
    definitions = [("technical", "行情与技术", "index_price"), ("valuation", "估值", "index_valuation"),
                   ("capital", "资金", "index_capital_flow"), ("macro", "宏观", "index_macro"),
                   ("sentiment", "新闻", "index_sentiment")]
    if ctx.target.index_style == "strategy":
        definitions.append(("strategy", "策略专项", "strategy_data"))
    prices = sorted({p.trade_date: p for p in ctx.price_data if p.symbol == ctx.target.symbol and is_positive_finite(p.close)}.values(), key=lambda p: p.trade_date)
    for key, title, record_key in definitions:
        section = sections.get(key) or {}
        metrics = section.get("metrics") or {}
        record = ctx.collection_records.get(record_key)
        applicable = ctx.target.market == "hk" or not (key == "capital" and ctx.target.index_style not in ("broad", "sector")
                          or key == "macro" and ctx.target.index_style != "broad"
                          or key == "sentiment" and ctx.target.index_style in ("strategy", "overseas"))
        status = {"ok": "ok", "partial": "partial"}.get(str(section.get("status")), "unavailable") if applicable else "not_applicable"
        # 采集状态和分析状态共同决定覆盖，旧值可分析不等于本次采集完整。
        if applicable and record:
            if record.result_state == "not_applicable":
                status = "not_applicable"
            elif record.result_state in {"failed", "empty"} and record.cache_status != "stale":
                status = "unavailable"
            elif status == "ok" and (record.result_state == "partial" or record.cache_status == "stale"
                                     or record.cache_state == "degraded" or record.error_kind or record.error_message):
                status = "partial"
        sample, cutoff, source = "未记录", "未记录", record.source if record else "未记录"
        if key == "technical" and prices:
            sample, cutoff = f"{prices[0].trade_date}至{prices[-1].trade_date}，{len(prices)}个交易日", str(prices[-1].trade_date)
        elif key == "valuation" and ctx.valuation_data:
            val = ctx.valuation_data
            sample = f"PE日频样本{val.pe_sample_count}个；PB日频样本{val.pb_sample_count}个"
            if val.pe_snapshot is not None and is_finite_number(val.pe_snapshot) and val.pe_sample_count == 0:
                sample += f"；PE快照{float(val.pe_snapshot):.2f}"
            if val.percentile_sample_start and val.percentile_sample_end:
                sample += f"；{val.percentile_sample_start}至{val.percentile_sample_end}"
            cutoff = "；".join(dict.fromkeys(str(d) for d in (val.pe_as_of, val.pe_snapshot_period or val.pe_snapshot_as_of, val.pb_as_of) if d)) or str(val.date)
            source = "；".join(dict.fromkeys(v for v in (val.pe_source_url, val.pe_snapshot_source_url, val.pb_source_url) if v)) or source
        elif key == "capital" and ctx.capital_flow:
            cutoff = str(ctx.capital_flow.date)
        elif key == "macro" and ctx.macro:
            dates = getattr(ctx.macro, "indicator_dates", {})
            cutoff = "；".join(f"{k} {v}" for k, v in dates.items()) or "未记录"
        elif key == "sentiment":
            sample = f"本次取得{len(metrics.get('items') or [])}条新闻"
        elif key == "strategy" and ctx.strategy_data:
            cutoff, source = str(ctx.strategy_data.as_of), ctx.strategy_data.source
            sample = f"{len(ctx.strategy_data.members)}个成分样本"
        fields = [{"label": "样本范围", "value": sample}, {"label": "数据截止", "value": cutoff}, {"label": "数据来源", "value": source}]
        notes = list(metrics.get("notes") or []) + list(metrics.get("valuation_notes") or [])
        if key == "technical":
            for window in (sections.get("performance") or {}).get("metrics", {}).get("windows") or []:
                notes.extend(f"{window.get('label', '')}：{note}" for note in window.get("notes") or [])
        if record and record.error_message:
            notes.append(record.error_message)
        elif record and record.error_kind:
            notes.append(f"采集异常：{record.error_kind}；具体原因未记录")
        elif record and record.result_state in {"partial", "failed", "empty"}:
            notes.append("本次采集部分返回或未取得可用数据；具体原因未记录")
        elif record and record.cache_state == "degraded":
            notes.append("采集记录标记降级；具体原因未记录")
        if record and record.cache_status:
            fields.append({"label": "缓存状态", "value": "过期缓存" if record.cache_status == "stale" else str(record.cache_status)})
        if applicable and status != "ok" and not notes:
            notes.append(section.get("summary") or "原因未记录")
        if status == "not_applicable":
            notes = ["当前指数不适用或未接入该项数据。"]
        for note in notes:
            if note and status != "ok":
                fields.append({"label": "分析限制", "value": str(note)})
        dimensions.append({"key": key, "title": title, "status": status, "fields": fields})
    quality = {"dimensions": dimensions}
    quality.update(compact_quality(quality))
    return quality


def valuation_rows(ctx: IndexAnalysisContext) -> list[dict[str, str]]:
    val = ctx.valuation_data
    if val is None:
        return []
    rows = []
    for label, value, sampled, basis in [
        ("PE", val.pe_ttm, val.pe_as_of or val.date, val.pe_basis or "滚动市盈率"),
        ("PE", val.pe_snapshot, val.pe_snapshot_period or val.pe_snapshot_as_of or val.date, val.pe_snapshot_basis or "官方快照，未声明TTM"),
        ("PB", val.pb, val.pb_as_of or val.date, val.pb_basis or "市净率"),
        ("股息率", val.dividend_yield, val.date, "来源披露口径"),
    ]:
        if value is not None and is_finite_number(value):
            rows.append({"label": label, "value": f"{float(value):.2f}" + ("%" if label == "股息率" else ""), "note": f"{sampled}；{basis}"})
    for label, value, count in [("PE历史分位", val.pe_percentile, val.pe_sample_count), ("PB历史分位", val.pb_percentile, val.pb_sample_count)]:
        valid = (val.valuation_valid and is_positive_finite(val.pe_ttm)) if label == "PE历史分位" else (val.pb_sample_count >= 252 and is_positive_finite(val.pb))
        if value is not None and is_valid_percentile(value) and valid:
            rows.append({"label": label, "value": f"{float(value):.1f}%", "note": f"近{val.percentile_lookback_years}年窗口，实际{count}个交易日；{val.percentile_sample_start}至{val.percentile_sample_end}"})
    return rows

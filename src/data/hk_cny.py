"""港股分析/展示副本的 CNY 折算；源金额、源币种和每日汇率单独保留。"""
from datetime import date, datetime
from typing import Any

from data import fx_rates
from data.schemas import AnalysisContext, FinancialData

# 已核验的金额币种覆盖：中资港股官方报表为 CNY；00005 的 Eastmoney 金额量级为 HKD，
# 目录部分期间标 USD 与金额不符，按指标/量级核验为 HKD。
AMOUNT_CURRENCY_OVERRIDES = {"00700.HK": "CNY", "09988.HK": "CNY", "00005.HK": "HKD"}

_FLOW_FIELDS = ("revenue", "net_profit", "deducted_net_profit", "basic_eps",
                "reported_revenue", "reported_revenue_ttm")
_CASH_FIELDS = ("operating_cash_flow", "capital_expenditure", "free_cash_flow", "operating_cash_flow_per_share")
_BALANCE_FIELDS = ("total_assets", "total_equity", "common_equity")

def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def financial_cny(row: FinancialData) -> tuple[FinancialData, dict[str, Any]]:
    """仅副本金额改变，源币种/来源证据不覆盖；金额币种以指标为准并允许官方覆盖。"""
    override = AMOUNT_CURRENCY_OVERRIDES.get(row.symbol)
    source = override or (row.report_currency if row.amount_currency_verified else "")
    # 兼容本轮修复前缓存：旧代码虽标 verified，仍在原因中保留了逐期币种冲突。
    foreign_conflict = source in {"USD", "HKD"} and any(
        f"CURRENCY={other}" in row.amount_currency_reason for other in {"USD", "HKD"} - {source}
    )
    if override:
        currency_note = f"官方核验金额币种 {override}" + (
            f"，覆盖指标 {row.report_currency}" if row.report_currency and row.report_currency != override else "")
    elif row.disclosure_currency and row.report_currency and row.disclosure_currency != row.report_currency:
        currency_note = (f"目录列报 {row.disclosure_currency} 与金额指标 {row.report_currency} 冲突；"
                         f"金额按指标 {row.report_currency}")
    else:
        currency_note = ""
    if not source:
        clone = row.model_copy(deep=True)
        reason = "金额币种未核实，禁止折算及 TTM"
        for name in (*_FLOW_FIELDS, *_CASH_FIELDS, *_BALANCE_FIELDS):
            setattr(clone, name, None)
        clone.amount_currency_verified = False
        clone.display_currency = ""
        rejected = fx_rates.FxRate("", "CNY", reason=reason).payload()
        return clone, {"source": row.model_dump(mode="json"), "amount_currency_override": override,
                       "currency_note": "；".join(filter(None, [currency_note, reason])),
                       "flow": rejected, "cash_flow": rejected, "balance": rejected,
                       "rejected_fields": {name: reason for name in (*_FLOW_FIELDS, *_CASH_FIELDS, *_BALANCE_FIELDS)
                                           if getattr(row, name) is not None}}
    end = row.fiscal_quarter
    start = row.period_start
    flow = (fx_rates.average_rate(source, start, end) if start is not None else
            fx_rates.rate_on(source, day=end) if source == "CNY" else
            fx_rates.FxRate(source, "CNY", reason="财报期间起点缺失，不能计算期间平均汇率"))
    balance = fx_rates.rate_on(source, day=end)
    cash_start = row.cash_flow_period_start or (start if row.cash_flow_basis == "reported" else None)
    cash = (fx_rates.average_rate(source, cash_start, end) if cash_start is not None else
            fx_rates.rate_on(source, day=end) if source == "CNY" else
            fx_rates.FxRate(source, "CNY", reason="现金流期间未核实，不能计算期间平均汇率"))
    if cash_start is not None and cash_start != start:
        cash = fx_rates.FxRate(source, "CNY", reason="现金流与利润表期间冲突，不折算或派生现金流")
    clone = row.model_copy(deep=True)
    clone.report_currency = source
    # 官方覆盖标的允许覆盖采集层的目录/指标冲突；否则仍要求源级核验。
    clone.amount_currency_verified = bool(source) and (bool(override) or (
        row.amount_currency_verified and not foreign_conflict))
    evidence = {"source": row.model_dump(mode="json"), "amount_currency_override": override,
                "currency_note": currency_note,
                "flow": flow.payload(), "cash_flow": cash.payload(), "balance": balance.payload()}
    rejected: dict[str, str] = {}
    for fields, fx in ((_FLOW_FIELDS, flow), (_CASH_FIELDS, cash), (_BALANCE_FIELDS, balance)):
        for name in fields:
            value = getattr(row, name)
            position = row.field_positions.get(name, {})
            # 官方覆盖已证明金额币种；旧 position 里的目录/指标冲突币种不再阻断折算。
            known = None if override else position.get("currency")
            unit = position.get("unit", row.amount_unit)
            scale = {"currency": 1, "million_currency": 1_000_000,
                     "billion_currency": 1_000_000_000}.get(unit)
            if name in {"basic_eps", "operating_cash_flow_per_share"}:
                scale = 1 if row.amount_unit in {"currency", "million_currency", "billion_currency"} else None
            compatible = clone.amount_currency_verified and (known is None or known == source)
            reason = ("金额币种冲突或未核实，禁止折算及 TTM：" + row.amount_currency_reason
                      if not compatible else "源金额单位不可证明，禁止折算及 TTM" if scale is None
                      else fx.reason if fx.rate is None else "")
            setattr(clone, name, value * scale * fx.rate
                    if value is not None and not reason and scale is not None and fx.rate is not None else None)
            if value is not None and reason:
                rejected[name] = reason
            if name in clone.field_positions:
                clone.field_positions[name]["currency"] = "CNY" if not reason else ""
                clone.field_positions[name]["unit"] = "currency" if not reason else unit
    evidence["rejected_fields"] = rejected
    clone.amount_unit = "currency"
    clone.display_currency = "CNY"
    clone.amount_currency_reason += f"；利润表：{flow.note()}；现金流：{cash.note()}；资产负债表：{balance.note()}"
    if rejected:
        clone.amount_currency_reason += "；" + "；".join(dict.fromkeys(rejected.values()))
    return clone, evidence


def _used_financial_periods(rows: list[FinancialData]) -> set[date]:
    """覆盖最新值、TTM、权益/年度现金流回退及实际趋势摘要的依赖期。"""
    ordered = sorted(rows, key=lambda row: row.fiscal_quarter, reverse=True)
    if not ordered:
        return set()
    latest = ordered[0]
    used = {latest.fiscal_quarter}
    used.update(row.fiscal_quarter for row in ordered[:8] if row.roe is not None)
    # 趋势摘要读取最近三期同比及其基期，连续同报告期摘要最多三年。
    for row in ordered[:3]:
        if latest.fiscal_quarter.year - row.fiscal_quarter.year > 2:
            continue
        used.add(row.fiscal_quarter)
        try:
            used.add(row.fiscal_quarter.replace(year=row.fiscal_quarter.year - 1))
        except ValueError:
            continue
    for years in (1, 2):
        try:
            used.add(latest.fiscal_quarter.replace(year=latest.fiscal_quarter.year - years))
        except ValueError:
            continue
    annuals = [row for row in ordered if row.period_start is not None
               and 330 <= (row.fiscal_quarter - row.period_start).days <= 380]
    # TTM 的年度必须恰好衔接本期起点，不以任意旧年度替代。
    for row in annuals:
        if (latest.period_start is not None and (latest.period_start - row.fiscal_quarter).days == 1
                or latest.period_start is None and row.fiscal_quarter == date(latest.fiscal_quarter.year - 1, 12, 31)):
            used.add(row.fiscal_quarter)
    for candidates in (
        [row for row in ordered if row.common_equity is not None or row.total_equity is not None],
        [row for row in annuals if row.capital_expenditure is not None],
    ):
        if candidates:
            used.add(candidates[0].fiscal_quarter)
    return used


def cny_context(ctx: AnalysisContext) -> AnalysisContext:
    """每批采集只折算一次；缓存和调用方原始上下文不变。"""
    if ctx.market != "hk" or ctx.currency_conversion:
        return ctx
    clone = ctx.model_copy(deep=True)
    evidence: dict[str, Any] = {"target": "CNY", "financial": [], "market": [], "notes": []}
    clone.currency_conversion = evidence
    clone.financial_data = [] if ctx.financial_data is not None else None
    used_periods = _used_financial_periods(ctx.financial_data or [])
    for row in ctx.financial_data or []:
        if row.fiscal_quarter not in used_periods:
            evidence["financial"].append({"source": row.model_dump(mode="json"),
                "status": "excluded", "note": "汇率未覆盖，未折算；历史内部期间不参与 CNY 分析与展示"})
            continue
        converted, facts = financial_cny(row)
        facts["status"] = "used"
        if clone.financial_data is not None:
            clone.financial_data.append(converted)
        evidence["financial"].append(facts)

    def market_rate(currency: str, day: date | None, source: Any, label: str) -> fx_rates.FxRate:
        requested = day or datetime.now().astimezone().date()
        fx = fx_rates.rate_on(currency, day=requested)
        evidence["market"].append({"label": label, "requested_day": day.isoformat() if day else None,
                                   "source_values": source, **fx.payload()})
        note_label = "同行市值" if label.startswith("同行市值") else label
        note = f"{note_label}：{fx.note()}" + ("；行情日期缺失，使用最近可得汇率" if day is None else "")
        if note not in evidence["notes"]:
            evidence["notes"].append(note)
        return fx

    prices = ctx.price_data or []
    price_day = max((row.trade_date for row in prices), default=None)
    if prices:
        fx = market_rate("HKD", price_day, [row.model_dump(mode="json") for row in prices], "行情价格及区间高低价")
        if fx.rate is None:
            clone.price_data = []
        else:
            for price in clone.price_data or []:
                for field in ("open", "close", "high", "low"):
                    setattr(price, field, getattr(price, field) * fx.rate)
        if clone.enriched_valuation is not None:
            evidence["source_valuation_points"] = [point.model_dump(mode="json")
                                                    for point in clone.enriched_valuation.daily_points]
            for point in clone.enriched_valuation.daily_points:
                point.close = point.close * fx.rate if point.close is not None and fx.rate is not None else None
    if ctx.current_quote.get("price") is not None:
        fx = market_rate("HKD", _day(ctx.current_quote.get("quoted_at")), ctx.current_quote, "当前报价")
        clone.current_quote["price"] = ctx.current_quote["price"] * fx.rate if fx.rate is not None else None
    val = clone.valuation_data
    if val is not None:
        val.valuation_notes = [f"源证据（未折算）：{note}" if "市值" in note else note
                               for note in val.valuation_notes]
        if val.market_cap is not None:
            fx = market_rate(val.market_cap_currency or "HKD", val.market_cap_as_of,
                             ctx.valuation_data.model_dump(mode="json") if ctx.valuation_data else {}, "总市值")
            val.market_cap = val.market_cap * fx.rate if fx.rate is not None else None
            val.valuation_notes.append(evidence["notes"][-1])
        # PE/PB 为源比率，保持原值；PS 重新用同币种金额计算。
        # 独立来源披露的 PS 比率可保留；旧派生 PS 必须重新核验分子/分母。
        if "派生" in val.ps_source or "÷" in val.ps_source:
            val.ps_ttm = None
        from data.financial_ttm import financial_ttm

        revenue = financial_ttm(clone.financial_data or [], "revenue", "hk", require_precision=True)
        if val.market_cap is not None and revenue is not None and revenue > 0:
            val.ps_ttm = val.market_cap / revenue
            val.ps_as_of = val.market_cap_as_of
            val.ps_source = "展示 CNY 总市值 ÷ CNY TTM 营收（期间平均汇率折算后派生，仅展示）"
        elif val.ps_ttm is None:
            val.valuation_notes.append("PS 暂不可用：CNY 总市值或 CNY TTM 营收未核实/缺失")

    def convert_peer(row: dict[str, Any], fallback: date | None = None) -> None:
        value = row.get("market_cap")
        if value is None:
            return
        fx = market_rate(row.get("currency") or "HKD", _day(row.get("as_of")) or fallback,
                         dict(row), f"同行市值 {row.get('symbol', '')}")
        row["market_cap"] = value * fx.rate if fx.rate is not None else None
        row["display_currency"] = "CNY" if fx.rate is not None else "汇率未核实，未统一 CNY"

    industry = clone.enriched_industry
    if industry is not None:
        comparison = industry.comparison
        block = comparison.get("valuation", {})
        as_of = _day(block.get("as_of"))
        for peer in [block.get("target", {}), *block.get("peers", [])]:
            convert_peer(peer, as_of)
        for peer in industry.top_peers:
            data = peer.model_dump()
            convert_peer(data, as_of)
            peer.market_cap = data["market_cap"]
    evidence["status"] = "partial" if any(
        fx.get("rate") is None for fx in evidence["market"]
    ) or any(facts[key]["rate"] is None for facts in evidence["financial"] if facts["status"] == "used"
             for key in ("flow", "cash_flow", "balance")) or any(
                 facts.get("rejected_fields") for facts in evidence["financial"]
             ) else "ok"
    evidence["note"] = ("展示 CNY；源金额/源币种/汇率及实际日期保留在 currency_conversion" if evidence["status"] == "ok"
                        else "汇率未核实，未统一 CNY；未折算金额仅保留在源证据，展示端不混用")
    from data.financial_ttm import financial_ttm

    ttm: dict[str, Any] = {field: financial_ttm(clone.financial_data or [], field, "hk", require_precision=True)
                           for field in ("revenue", "net_profit")}
    evidence["ttm"] = ttm
    if any(value is None for value in ttm.values()):
        reasons = list(dict.fromkeys(
            f"{facts['source']['fiscal_quarter']} {reason}"
            for facts in evidence["financial"]
            for field, reason in facts.get("rejected_fields", {}).items()
            if field in {"revenue", "net_profit"}
        ))
        ttm["reason"] = "；".join(reasons) or "必需期间/金额或原生精度不足，无法构建 TTM"
        evidence["note"] += "；TTM 不可用：" + ttm["reason"]
    return clone

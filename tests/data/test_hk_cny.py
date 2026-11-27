"""折算副本、同币种派生与多输出共用事实。汇率均为显式离线测试值。"""
from datetime import date
from typing import Any

import pytest

from analysis.financial import FinancialAnalyzer
from analysis.valuation import ValuationAnalyzer
from data.fx_rates import FxRate
from data.hk_cny import cny_context
from data.schemas import AnalysisContext, FinancialData, PriceData, ValuationData
from push.summary import build_stock_summary
from report.payload import build_stock_payload
from report.scoring import build_report
from report.signal import SignalConfig


@pytest.fixture
def fx(mocker):
    def spot(currency, target="CNY", *, day):
        return FxRate(currency, target, {"HKD": 0.8, "USD": 7, "CNY": 1}[currency], (day,))

    def average(currency, start, end, target="CNY"):
        return FxRate(currency, target, {"HKD": 0.9, "USD": 6, "CNY": 1}[currency], (start, end))

    mocker.patch("data.fx_rates.rate_on", side_effect=spot)
    mocker.patch("data.fx_rates.average_rate", side_effect=average)


def context(symbol, currency) -> Any:
    end = date(2025, 12, 31)
    amounts: dict[str, Any] = {"revenue": 100, "net_profit": 20, "total_equity": 60, "common_equity": 50,
               "basic_eps": 2, "capital_expenditure": 3, "free_cash_flow": 7, "operating_cash_flow": 10}
    row = FinancialData(symbol=symbol, fiscal_quarter=end, period_start=date(2025, 1, 1),
        period_basis="cumulative", amount_currency_verified=True, report_currency=currency,
        disclosure_currency=currency, amount_unit="currency", **amounts,
        field_precision={key: "source_native" for key in amounts},
        field_positions={"common_equity": {"currency": currency}}, cash_flow_basis="reported")
    return AnalysisContext(symbol=symbol, name=symbol, market="hk", financial_data=[row],
        valuation_data=ValuationData(symbol=symbol, date=end, market_cap=1000,
            market_cap_as_of=end, market_cap_currency="HKD", pe_ttm=10, pb=2),
        price_data=[PriceData(symbol=symbol, trade_date=end, open=10, high=12, low=8, close=11, volume=1)])


@pytest.mark.parametrize("symbol,currency,profit,equity", [
    ("00700.HK", "CNY", 20, 50), ("09988.HK", "CNY", 20, 50),
    ("00005.HK", "HKD", 18, 40), ("00388.HK", "HKD", 18, 40)])
def test_amounts_eps_ratios_and_source_preservation(fx, symbol, currency, profit, equity):
    raw = context(symbol, currency)
    converted: Any = cny_context(raw)
    latest = converted.financial_data[0]
    assert latest.net_profit == profit and latest.common_equity == equity
    assert latest.report_currency == currency and latest.disclosure_currency == currency
    assert latest.display_currency == "CNY"
    assert converted.price_data[0].close == pytest.approx(8.8)
    assert converted.valuation_data.market_cap == 800
    assert converted.valuation_data.pe_ttm == 10 and converted.valuation_data.pb == 2
    assert converted.valuation_data.market_cap_currency == "HKD"  # 源字段不改名为CNY
    assert raw.valuation_data.market_cap == 1000 and raw.financial_data[0].net_profit == 20
    assert cny_context(converted) is converted
    facts = converted.currency_conversion["financial"][0]
    assert facts["source"]["net_profit"] == 20
    assert facts["flow"]["currency"] == currency
    assert facts["balance"]["dates"] == ["2025-12-31"]
    financial = FinancialAnalyzer().analyze(converted)
    valuation = ValuationAnalyzer().analyze(converted)
    assert financial.metrics["roe"] == pytest.approx(profit / equity)
    assert valuation.metrics["ps_ttm"] == pytest.approx(800 / latest.revenue)
    assert valuation.metrics["pb"] == 2


def test_missing_fx_exposes_reason_and_never_mixes_currencies():
    raw = context("00388.HK", "HKD")
    converted: Any = cny_context(raw)
    assert converted.financial_data[0].revenue is None
    assert converted.valuation_data.market_cap is None and converted.valuation_data.ps_ttm is None
    assert converted.currency_conversion["status"] == "partial"
    assert "汇率未核实，未统一 CNY" in converted.currency_conversion["note"]
    assert converted.currency_conversion["financial"][0]["source"]["revenue"] == 100


def test_markdown_json_web_contract_and_push_share_fact(fx):
    raw = context("00700.HK", "CNY")
    raw.current_quote = {"status": "ok", "symbol": "00700.HK", "price": 11,
                         "quoted_at": "2025-12-31T14:16:55+08:00", "source": "腾讯港股快照"}
    ctx = cny_context(raw)
    results = [FinancialAnalyzer().analyze(ctx), ValuationAnalyzer().analyze(ctx)]
    payload = build_stock_payload(ctx.symbol, ctx.name, results, {}, ctx, no_llm=True)
    markdown = build_report(ctx.symbol, ctx.name, results, {}, ctx, no_llm=True)
    push = build_stock_summary(ctx.symbol, ctx.name, results, ctx, SignalConfig())
    assert payload["overview"]["latest_close"] == pytest.approx(8.8)
    assert payload["dimensions"]["financial"]["metrics"]["basic_eps"] == 2
    assert payload["currency_conversion"]["financial"][0]["source"]["report_currency"] == "CNY"
    assert "8.80" in markdown and "8.8" in push
    assert "人民币折算 HKD→CNY 0.8（2025-12-31，ECB）" in markdown
    assert "展示 CNY" in push and "源币种 HKD" in push and "2025-12-31" in push
    note = payload["overview"]["current_price"]["note"]
    assert note == "交易中；2025-12-31 14:16 UTC+8；人民币折算 HKD→CNY 0.8（2025-12-31，ECB）"
    for internal in ("currency_conversion", "行情价格及区间高低价", "当前报价：", "展示 CNY", "源币种 HKD"):
        assert internal not in note
    assert "| 行情来源 | 腾讯港股快照 |" in markdown


def test_markdown_hides_duplicate_source_row_for_close_fallback(fx):
    ctx = cny_context(context("00700.HK", "CNY"))
    markdown = build_report(ctx.symbol, ctx.name, [], {}, ctx, no_llm=True)
    assert "报价暂不可用，回退日线收盘价；截至 2025-12-31" in markdown
    assert "| 行情来源 |" not in markdown


def test_unknown_source_currency_and_missing_period_are_not_converted():
    ctx = context("00388.HK", "HKD")
    ctx.financial_data[0].period_start = None
    result: Any = cny_context(ctx)
    assert "期间起点缺失" in result.currency_conversion["financial"][0]["flow"]["reason"]
    ctx.financial_data[0].disclosure_currency = ctx.financial_data[0].report_currency = ""
    result = cny_context(ctx)
    assert result.financial_data is not None
    assert result.financial_data[0].revenue is None


def test_conflicting_cashflow_period_is_not_converted(fx):
    ctx = context("00005.HK", "HKD")
    ctx.financial_data[0].cash_flow_period_start = date(2025, 7, 1)
    result = cny_context(ctx)
    assert result.financial_data is not None
    assert result.financial_data[0].operating_cash_flow is None
    assert result.financial_data[0].net_profit == 18
    facts = result.currency_conversion["financial"][0]
    assert "期间冲突" in facts["cash_flow"]["reason"]
    assert facts["source"]["operating_cash_flow"] == 10


@pytest.mark.parametrize("currency", ["USD", "HKD"])
def test_converted_cashflow_and_trend_keep_evidence_gate(fx, currency):
    from analysis.financial_scoring import cashflow_compatible
    from data.financial_metrics import comparable_fields
    from data.hk_cny import financial_cny
    from tests.data.test_hk_financial_evidence import hk_row

    row = hk_row(symbol="00388.HK", report_currency=currency)
    for position in row.field_positions.values():
        position["currency"] = currency
    converted, _ = financial_cny(row)
    assert cashflow_compatible(converted)
    previous = converted.model_copy(deep=True)
    previous.fiscal_quarter = date(2025, 9, 30)
    previous.period_start = date(2025, 1, 1)
    for position in previous.field_positions.values():
        position["start"] = "2025-01-01"
    assert comparable_fields(previous, converted, "revenue")


def test_peer_market_cap_keeps_source_and_actual_fx_date(fx):
    from data.schemas import EnrichedIndustry, PeerComparison

    ctx = context("00700.HK", "CNY")
    ctx.enriched_industry = EnrichedIndustry(top_peers=[PeerComparison(symbol="09988.HK", market_cap=200)],
        comparison={"valuation": {"as_of": "2025-12-31", "peers": [
            {"symbol": "09988.HK", "market_cap": 200, "as_of": "2025-12-31"}]}})
    converted: Any = cny_context(ctx)
    assert converted.enriched_industry.top_peers[0].market_cap == 160
    row = converted.enriched_industry.comparison["valuation"]["peers"][0]
    assert row["market_cap"] == 160 and row["display_currency"] == "CNY"
    evidence = next(item for item in converted.currency_conversion["market"] if item["label"] == "同行市值 09988.HK")
    assert evidence["source_values"]["market_cap"] == 200 and evidence["dates"] == ["2025-12-31"]


@pytest.mark.parametrize("missing_year", [None, 2025, 2026])
def test_hsbc_old_uncovered_periods_do_not_block_cny_analysis(mocker, missing_year):
    """1998/1999 无覆盖不请求；必需期缺汇率时不混入 USD 或退回旧年。"""
    raw = context("00005.HK", "HKD")
    template = raw.financial_data[0]
    rows = []
    for year, month, revenue in [(2026, 9, 90), (2025, 12, 100), (2025, 9, 70),
                                  (1999, 12, 9999), (1998, 12, 8888)]:
        row = template.model_copy(deep=True)
        row.fiscal_quarter = date(year, month, 30 if month == 9 else 31)
        row.period_start = date(year, 1, 1)
        row.revenue = revenue
        rows.append(row)
    raw.financial_data = rows

    def rate(currency, target="CNY", *, day):
        assert day.year >= 2025, "不应为历史内部期间取汇率"
        return FxRate(currency, target, None if day.year == missing_year else 7, (day,),
                      reason="HTTP 404" if day.year == missing_year else "")

    def average(currency, start, end, target="CNY"):
        return rate(currency, target, day=end)

    mocker.patch("data.fx_rates.rate_on", side_effect=rate)
    mocker.patch("data.fx_rates.average_rate", side_effect=average)
    converted = cny_context(raw)
    assert converted.financial_data is not None
    assert {row.fiscal_quarter.year for row in converted.financial_data} == {2025, 2026}
    evidence = converted.currency_conversion["financial"]
    excluded = [item for item in evidence if item["status"] == "excluded"]
    assert len(excluded) == 2 and excluded[0]["source"]["revenue"] == 9999
    assert all("汇率未覆盖，未折算" in item["note"] for item in excluded)
    latest = max(converted.financial_data, key=lambda row: row.fiscal_quarter)
    assert latest.fiscal_quarter == date(2026, 9, 30)
    assert latest.revenue == (None if missing_year == 2026 else 630)
    financial = FinancialAnalyzer().analyze(converted)
    valuation = ValuationAnalyzer().analyze(converted)
    if missing_year is None:
        # CNY TTM = 630 + 700 - 490 = 840，不含 1998/1999 源值。
        assert valuation.metrics["ps_ttm"] == pytest.approx(7000 / 840)
        assert isinstance(financial.metrics["roe"], float)
    else:
        assert valuation.metrics["ps_ttm"] is None
        assert financial.metrics["roe"] == "TTM 不可用"
        failed = next(item for item in evidence if item["source"]["fiscal_quarter"].startswith(str(missing_year)))
        assert failed["flow"]["rate"] is None
        if missing_year == 2026:
            assert "汇率未核实" in latest.amount_currency_reason
    assert valuation.metrics["pe_ttm"] == 10 and valuation.metrics["pb"] == 2
    assert raw.financial_data[-1].revenue == 8888


@pytest.mark.parametrize("unit,scale", [("currency", 1), ("million_currency", 1_000_000),
                                      ("billion_currency", 1_000_000_000)])
def test_hsbc_mixed_currencies_normalize_units_before_ttm(fx, unit, scale):
    """手算：90 HKD × .9 + 100 USD × 6 - 70 HKD × .9 = 618 CNY。"""
    from data.financial_ttm import financial_ttm

    raw = context("00388.HK", "HKD")
    template = raw.financial_data[0]
    rows = []
    for year, month, currency, revenue in [(2026, 6, "HKD", 90), (2025, 12, "USD", 100),
                                          (2025, 6, "HKD", 70)]:
        row = template.model_copy(deep=True)
        row.fiscal_quarter = date(year, month, 30 if month == 6 else 31)
        row.period_start = date(year, 1, 1)
        row.disclosure_currency = row.report_currency = currency
        row.amount_unit = unit
        row.revenue, row.net_profit = revenue, revenue / 10
        row.field_positions = {}
        rows.append(row)
    raw.financial_data = rows
    converted = cny_context(raw)
    assert converted.financial_data is not None
    assert financial_ttm(converted.financial_data, "revenue", "hk", require_precision=True) == pytest.approx(618 * scale)
    assert financial_ttm(converted.financial_data, "net_profit", "hk", require_precision=True) == pytest.approx(61.8 * scale)
    assert rows[1].revenue == 100 and rows[1].report_currency == "USD"


@pytest.mark.parametrize("fault", ["unit", "currency", "unverified"])
def test_hsbc_required_annual_conflict_or_unknown_unit_rejects_ttm(fx, fault):
    from data.financial_ttm import financial_ttm

    raw = context("00388.HK", "USD")
    annual = raw.financial_data[0]
    if fault == "unit":
        annual.field_positions["revenue"] = {"unit": "万元?", "currency": "USD"}
    elif fault == "currency":
        annual.field_positions["revenue"] = {"unit": "currency", "currency": "HKD"}
    else:
        annual.amount_currency_verified = False
        annual.amount_currency_reason = "目录 USD 与金额 HKD 冲突"
    converted = cny_context(raw)
    assert converted.financial_data is not None
    assert financial_ttm(converted.financial_data, "revenue", "hk", require_precision=True) is None
    assert converted.valuation_data is not None and converted.valuation_data.ps_ttm is None
    assert converted.currency_conversion["financial"][0]["rejected_fields"]["revenue"]
    assert converted.currency_conversion["status"] == "partial"

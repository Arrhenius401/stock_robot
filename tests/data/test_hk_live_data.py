"""港股真实东财响应回归：样本按最新五个报告日期裁切。"""
import json
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from numpy import bool_

from data.akshare import AkShareAdapter
from data.enrichers.financial_enricher import FinancialEnricher
from data.enrichers.valuation_enricher import ValuationEnricher
from data.schemas import AnalysisContext, FinancialData, SufficiencyLevel, ValuationData
from utils.symbols import resolve_name

RESOURCES = Path(__file__).parents[1] / "resources" / "data"


@pytest.fixture(autouse=True)
def isolate_tencent_history(mocker):
    """旧东财样本测试显式隔离新增主源，不能触网。"""
    mocker.patch("data.akshare.tencent_history", return_value=[])
    mocker.patch("data.akshare.financial_report_directory", return_value=[])


def sample(kind):
    return json.loads((RESOURCES / f"eastmoney-00700-{kind}.json").read_text(encoding="utf-8"))


def financial_frames():
    return [sample(f"{kind}-report") for kind in ("income", "balance", "cash-flow")]


def test_real_long_statements_are_five_periods_not_item_rows(mocker):
    mocker.patch("data.akshare.financial_statement", side_effect=financial_frames())
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="financial")
    assert len(rows) == 5
    assert len({row.fiscal_quarter for row in rows}) == 5
    latest = max(rows, key=lambda row: row.fiscal_quarter)
    assert latest.fiscal_quarter == date(2026, 6, 30)
    assert latest.revenue == 396431000000
    assert latest.net_profit == 114115000000
    assert latest.basic_eps == 12.639
    assert latest.total_assets == 2159394000000
    assert latest.total_equity == 1222513000000
    assert latest.common_equity == 1135853000000
    assert latest.operating_cash_flow == 154061000000
    assert latest._data_source.startswith("东方财富")


def test_long_statements_skip_bad_dates_and_keep_valid_duplicate(mocker):
    frames = financial_frames()
    original = frames[0][0]
    bad = {**original, "REPORT_DATE": "bad", "AMOUNT": 12}
    missing = {**original, "AMOUNT": float("nan")}
    wrong = {**original, "SECUCODE": "00005.HK", "SECURITY_CODE": "00005", "AMOUNT": 999}
    frames[0].extend([bad, missing, wrong, original])
    mocker.patch("data.akshare.financial_statement", side_effect=frames)
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="financial")
    assert len(rows) == 5
    assert max(rows, key=lambda row: row.fiscal_quarter).revenue == 396431000000


def test_snapshot_uses_ttm_and_actual_timestamp(mocker):
    response = mocker.patch("requests.get").return_value
    response.json.return_value = sample("valuation-snapshot")
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="valuation")
    assert len(rows) == 1
    assert rows[0].pe_ttm == 14.23
    assert rows[0].pb == 2.9
    assert rows[0].date == datetime.fromtimestamp(1792138094, tz=UTC).astimezone(timezone(timedelta(hours=8))).date()
    assert rows[0]._data_source.startswith("东方财富")


@pytest.mark.parametrize("payload", [{"data": None}, {"data": {"f57": "00005"}},
    {"data": {"f57": "00700", "f86": 1792138094, "f164": "-", "f167": "nan"}},
    {"data": {"f57": "00700", "f86": "-", "f164": 12}}])
def test_invalid_snapshot_does_not_invent_valuation(mocker, payload):
    mocker.patch("requests.get").return_value.json.return_value = payload
    assert AkShareAdapter().fetch("00700.HK", market="hk", data_type="valuation") == []


def test_hk_name_never_queries_a_share_universe(mocker):
    mocker.patch("requests.get").return_value.json.return_value = sample("valuation-snapshot")
    mainland = mocker.patch("utils.symbols._ak_code_name", side_effect=AssertionError("不应扫 A 股"))
    assert resolve_name("hk00700") == "腾讯控股"
    mainland.assert_not_called()


def test_hk_enrichment_keeps_measured_snapshot_without_price_or_ttm(mocker):
    shares = mocker.patch("data.enrichers.valuation_enricher.get_total_shares", side_effect=AssertionError)
    ctx = AnalysisContext(symbol="00700.HK", name="腾讯控股", market="hk", valuation_data=ValuationData(
        symbol="00700.HK", date=date(2026, 10, 16), pe_ttm=14.23, pb=2.9))
    ValuationEnricher().enrich(ctx)
    assert ctx.sufficiency.valuation.level == SufficiencyLevel.PARTIAL
    assert ctx.sufficiency.valuation.sample_count == 1
    assert ctx.enriched_valuation is not None
    assert ctx.enriched_valuation.daily_points == []
    shares.assert_not_called()


def test_hk_financial_reason_does_not_claim_quarter_ttm():
    ctx = AnalysisContext(symbol="00700.HK", name="腾讯控股", market="hk", financial_data=[FinancialData(
        symbol="00700.HK", fiscal_quarter=date(2026, 6, 30), revenue=100, net_profit=20)])
    FinancialEnricher().enrich(ctx)
    assert ctx.sufficiency.financial.level == SufficiencyLevel.PARTIAL
    assert "季报" not in ctx.sufficiency.financial.reason
    assert "TTM" not in ctx.sufficiency.financial.reason


def test_hk_history_request_is_bounded_unadjusted_and_single_target(mocker):
    request = mocker.patch("requests.get")
    request.return_value.json.return_value = {"data": {"code": "00700", "klines": []}}
    assert AkShareAdapter().fetch("00700.HK", market="hk", data_type="price") == []
    request.assert_called_once()
    kwargs = request.call_args.kwargs
    assert kwargs["timeout"] == 10
    params = kwargs["params"]
    assert params["secid"] == "116.00700"
    assert params["fqt"] == "0"
    assert int(params["lmt"]) == 366
    assert (datetime.strptime(params["end"], "%Y%m%d").astimezone().date()
            - datetime.strptime(params["beg"], "%Y%m%d").astimezone().date()).days == 365


def test_hk_bad_history_rows_do_not_discard_good_rows(mocker):
    mocker.patch("data.akshare.daily_history", return_value=[
        "bad", "2026-10-01,500,505,510,495,123456,0,0,-,0,0",
        "2026-10-02,nan,505,510,495,123456,0,0,1,0,0",
        "2026-09-01,500,505,510,495,123456,0,0,1,0,0"])
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="price",
        start_date="20261001", end_date="20261002")
    assert len(rows) == 1
    assert rows[0].close == 505
    assert rows[0].change_pct is None


def test_hk_network_failure_diagnostic_identifies_real_provider(mocker):
    mocker.patch("requests.get", side_effect=TimeoutError)
    adapter = AkShareAdapter()
    assert adapter.fetch("00700.HK", market="hk", data_type="valuation") == []
    diagnostic = adapter.get_collection_error()
    assert diagnostic is not None
    assert diagnostic.source == "百度港股市净率"
    assert "东方财富港股估值快照" in diagnostic.error_message
    assert "百度港股市盈率(TTM)" in diagnostic.error_message
    assert diagnostic.error_kind == "timeout"


def test_hk_absent_snapshot_does_not_derive_from_financials(mocker):
    shares = mocker.patch("data.enrichers.valuation_enricher.get_total_shares", side_effect=AssertionError)
    ctx = AnalysisContext(symbol="00700.HK", name="腾讯控股", market="hk")
    ValuationEnricher().enrich(ctx)
    assert ctx.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT
    assert ctx.enriched_valuation is None
    shares.assert_not_called()


def test_optional_balance_failure_keeps_income_and_cash_flow(mocker):
    frames = financial_frames()
    mocker.patch("data.akshare.financial_statement", side_effect=[frames[0], TimeoutError(), frames[2]])
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="financial")
    assert len(rows) == 5
    latest = max(rows, key=lambda row: row.fiscal_quarter)
    assert latest.revenue == 396431000000
    assert latest.total_assets is None
    assert latest.operating_cash_flow == 154061000000


def test_optional_cash_flow_failure_keeps_income_and_balance(mocker):
    frames = financial_frames()
    mocker.patch("data.akshare.financial_statement", side_effect=[frames[0], frames[1], TimeoutError()])
    adapter = AkShareAdapter()
    rows = adapter.fetch("00700.HK", market="hk", data_type="financial")
    assert len(rows) == 5
    latest = max(rows, key=lambda row: row.fiscal_quarter)
    assert latest.revenue == 396431000000
    assert latest.total_assets == 2159394000000
    assert latest.operating_cash_flow is None
    diagnostic = adapter.get_collection_error()
    assert diagnostic is not None
    assert "现金流量表" in diagnostic.source


def test_snapshot_date_uses_hong_kong_day_not_server_timezone(mocker):
    payload = sample("valuation-snapshot")
    payload["data"]["f86"] = datetime(2026, 10, 18, 18, tzinfo=UTC).timestamp()
    mocker.patch("requests.get").return_value.json.return_value = payload
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="valuation")
    assert rows[0].date == date(2026, 10, 19)


@pytest.mark.parametrize("bad_value", [True, False, bool_(True), bool_(False)])
def test_boolean_amounts_are_not_financial_values(mocker, bad_value):
    frames = financial_frames()
    for row in frames[0]:
        if row["STD_ITEM_CODE"] == "004001001":
            row["AMOUNT"] = bad_value
    mocker.patch("data.akshare.financial_statement", side_effect=frames)
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="financial")
    assert len(rows) == 5
    assert all(row.revenue is None for row in rows)


@pytest.mark.parametrize("bad_value", [True, False, bool_(True), bool_(False)])
def test_boolean_snapshot_fields_are_not_valuation_values(mocker, bad_value):
    payload = sample("valuation-snapshot")
    payload["data"].update(f164=bad_value, f167=bad_value)
    mocker.patch("requests.get").return_value.json.return_value = payload
    assert AkShareAdapter().fetch("00700.HK", market="hk", data_type="valuation") == []


def test_nat_financial_row_does_not_create_or_discard_report_periods(mocker):
    frames = financial_frames()
    row = {**frames[0][0], "REPORT_DATE": pd.NaT}
    frames[0].append(row)
    mocker.patch("data.akshare.financial_statement", side_effect=frames)
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="financial")
    assert len(rows) == 5
    assert all(row.fiscal_quarter is not pd.NaT for row in rows)


def test_financial_request_uses_direct_bounded_same_source(mocker):
    from data.hk_eastmoney import financial_statement

    request = mocker.patch("requests.get")
    records = sample("income-report")
    request.return_value.json.return_value = {"success": True, "result": {
        "pages": 1, "count": len(records), "data": records}}
    assert financial_statement("00700", "利润表") == records
    request.assert_called_once()
    assert request.call_args.args[0] == "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    assert request.call_args.kwargs["timeout"] == 10
    params = request.call_args.kwargs["params"]
    assert params["reportName"] == "RPT_HKF10_FN_INCOME_PC"
    assert params["filter"] == '(SECUCODE="00700.HK")'
    assert int(params["pageSize"]) == 10000


@pytest.mark.parametrize("payload", [None, [], {"success": False}, {"result": None},
    {"success": True, "result": {"data": []}},
    {"success": True, "result": {"pages": 2, "count": 20000, "data": []}},
    {"success": True, "result": {"pages": 1, "count": 1, "data": [{"SECUCODE": "00005.HK"}]}},
    {"success": True, "result": {"pages": 1, "count": 1, "data": [None]}}])
def test_financial_request_rejects_malformed_mismatched_or_incomplete_response(mocker, payload):
    from data.hk_eastmoney import financial_statement

    mocker.patch("requests.get").return_value.json.return_value = payload
    with pytest.raises(ValueError):
        financial_statement("00700", "利润表")


def test_financial_request_timeout_is_diagnosed_without_sdk_retry(mocker):
    request = mocker.patch("requests.get", side_effect=TimeoutError)
    adapter = AkShareAdapter()
    assert adapter.fetch("00700.HK", market="hk", data_type="financial") == []
    request.assert_called_once()
    record = adapter.get_collection_error()
    assert record is not None
    assert record.source == "东方财富港股财报"
    assert record.error_kind == "timeout"

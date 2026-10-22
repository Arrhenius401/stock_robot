"""财报目录只补明确元信息，不替换金额、推断币种或扩大采集。"""
from datetime import date

import pytest

from analysis.financial import FinancialAnalyzer
from analysis.financial_trends import build_financial_trends
from data import hk_eastmoney
from data.akshare import AkShareAdapter
from data.enrichers.financial_enricher import FinancialEnricher
from data.schemas import AnalysisContext


def directory_row(year=2026, **extra):
    """字段和口径来自腾讯目录实样；跨年数值为隔离测试构造。"""
    return {"SECUCODE": "00700.HK", "SECURITY_CODE": "00700", "REPORT_DATE": f"{year}-06-30",
            "START_DATE": f"{year}-01-01", "CURRENCY": "人民币", "ACCOUNT_STANDARD": "国际会计准则",
            "REPORT_TYPE": "中报", **extra}


@pytest.fixture
def source(mocker):
    frames = [[], [], []]
    for year, amount in ((2025, 100), (2026, 120)):
        common = {"SECUCODE": "00700.HK", "SECURITY_CODE": "00700",
                  "REPORT_DATE": f"{year}-06-30", "START_DATE": f"{year}-01-01"}
        frames[0].extend([{**common, "STD_ITEM_CODE": code, "AMOUNT": amount}
                          for code in ("004001001", "004025002")])
        frames[1].append({**common, "STD_ITEM_CODE": "004009999", "AMOUNT": 500})
        frames[2].append({**common, "STD_ITEM_CODE": "003999", "AMOUNT": amount})
    mocker.patch("data.akshare.financial_statement", side_effect=frames)
    request = mocker.patch("data.akshare.financial_report_directory",
                           return_value=[directory_row(2025), directory_row()])
    return frames, request


def test_directory_request_is_same_source_single_bounded_call(mocker):
    rows = [directory_row()]
    request = mocker.patch("requests.get")
    request.return_value.json.return_value = {"success": True, "result": {
        "data": [{"REPORT_LIST": rows}]}}
    assert hk_eastmoney.financial_report_directory("00700") == rows
    request.assert_called_once()
    assert request.call_args.args[0] == "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    assert request.call_args.kwargs["timeout"] == 10
    params = request.call_args.kwargs["params"]
    assert params["reportName"] == "RPT_CUSTOM_HKSK_APPFN_CASHFLOW_SUMMARY"
    assert params["filter"] == '(SECUCODE="00700.HK")'
    assert params["pageSize"] == "1" and params["pageNumber"] == "1"


@pytest.mark.parametrize("payload", [None, [], {"success": False}, {"success": True, "result": None},
    {"success": True, "result": {"data": []}},
    {"success": True, "result": {"data": [{"REPORT_LIST": None}]}},
    {"success": True, "result": {"data": [{"REPORT_LIST": [None]}]}},
    {"success": True, "result": {"data": [{"REPORT_LIST": [directory_row(SECURITY_CODE="00005")]}]}},
    {"success": True, "result": {"data": [{"REPORT_LIST": [directory_row(SECUCODE="00005.HK")]}]}},
    {"success": True, "result": {"data": [{"REPORT_LIST": [directory_row()]}] * 2}},
])
def test_directory_rejects_invalid_or_wrong_symbol_response(mocker, payload):
    mocker.patch("requests.get").return_value.json.return_value = payload
    with pytest.raises(ValueError):
        hk_eastmoney.financial_report_directory("00700")


def test_directory_fills_disclosure_metadata_not_amount_currency(source):
    _, request = source
    rows = AkShareAdapter()._fetch_hk_financial("00700.HK")
    request.assert_called_once_with("00700")
    assert [r.revenue for r in rows] == [100, 120]
    assert [r.net_profit for r in rows] == [100, 120]
    assert all(r.total_assets == 500 for r in rows)
    assert all(r.report_currency == "" and r.disclosure_currency == "CNY"
               and "国际会计准则" in r.reporting_basis for r in rows)
    assert all(r.cash_flow_basis == "unknown" for r in rows)
    assert rows[-1].period_start == date(2026, 1, 1)
    assert build_financial_trends(rows, "00700.HK")["highlights"] == []


@pytest.mark.parametrize("extra", [
    {"START_DATE": "2026-04-01"}, {"START_DATE": "bad"}, {"START_DATE": None},
    {"REPORT_DATE": "2026-09-30"}, {"CURRENCY": ""}, {"CURRENCY": "未知"},
    {"ACCOUNT_STANDARD": ""}, {"ACCOUNT_STANDARD": "未知"},
    {"ACCOUNT_STANDARD": "nan"}, {"ACCOUNT_STANDARD": "NaN"}, {"ACCOUNT_STANDARD": "null"},
    {"ACCOUNT_STANDARD": "NONE"}, {"ACCOUNT_STANDARD": "unknown"}, {"ACCOUNT_STANDARD": " n/A "},
    {"SECUCODE": "00005.HK", "SECURITY_CODE": "00005"},
])
def test_directory_does_not_fill_ambiguous_or_nonmatching_period(source, extra):
    _, request = source
    request.return_value = [directory_row(2025), directory_row(**extra)]
    rows = AkShareAdapter()._fetch_hk_financial("00700.HK")
    assert rows[-1].revenue == 120 and rows[-1].report_currency == ""
    assert build_financial_trends(rows, "00700.HK")["highlights"] == []


@pytest.mark.parametrize("extra", [{"CURRENCY": "港币"}, {"START_DATE": "2026-04-01"},
                                  {"ACCOUNT_STANDARD": "中国会计准则"}, {"CURRENCY": ""}])
def test_conflicting_directory_duplicates_are_not_arbitrarily_selected(source, extra):
    _, request = source
    request.return_value.append(directory_row(**extra))
    rows = AkShareAdapter()._fetch_hk_financial("00700.HK")
    assert rows[-1].report_currency == ""
    assert build_financial_trends(rows, "00700.HK")["highlights"] == []


def test_currency_aliases_and_identical_duplicates_are_not_conflicts(source):
    frames, request = source
    for row in frames[0] + frames[2]:
        row["CURRENCY_CODE"] = "CNY"
    request.return_value.append(directory_row(CURRENCY="CNY"))
    rows = AkShareAdapter()._fetch_hk_financial("00700.HK")
    assert rows[-1].report_currency == "CNY" and rows[-1].cash_flow_basis == "reported"


def test_statement_currency_conflict_is_not_overwritten_by_directory(source):
    frames, _ = source
    for row in frames[0]:
        if row["REPORT_DATE"] == "2026-06-30":
            row["CURRENCY"] = "HKD"
    rows = AkShareAdapter()._fetch_hk_financial("00700.HK")
    assert rows[-1].revenue == 120 and rows[-1].report_currency == "HKD"
    assert rows[-1].disclosure_currency == "CNY"
    assert build_financial_trends(rows, "00700.HK")["highlights"] == []


def test_cashflow_conflict_only_disables_cashflow_comparison(source):
    frames, _ = source
    for row in frames[0] + frames[2]:
        row["CURRENCY"] = "CNY"
    frames[2][-1]["CURRENCY"] = "HKD"
    rows = AkShareAdapter()._fetch_hk_financial("00700.HK")
    assert rows[-1].report_currency == "CNY" and rows[-1].cash_flow_basis == "unknown"
    assert rows[-1].operating_cash_flow == 120
    ctx = AnalysisContext(symbol="00700.HK", name="腾讯控股", market="hk", financial_data=rows)
    metric = FinancialAnalyzer().analyze(ctx).metrics["amount_basis"]
    assert "金额币种：CNY" in metric
    assert "仅代表已核实的利润表金额" in metric


def test_changed_accounting_standard_is_not_joined_into_a_trend(source):
    frames, request = source
    for row in frames[0] + frames[2]:
        row["CURRENCY_CODE"] = "CNY"
    request.return_value[-1]["ACCOUNT_STANDARD"] = "中国会计准则"
    rows = AkShareAdapter()._fetch_hk_financial("00700.HK")
    assert all(r.report_currency == "CNY" for r in rows)
    assert build_financial_trends(rows, "00700.HK")["highlights"] == []


def test_directory_timeout_preserves_three_statement_values_and_logs(source, caplog):
    _, request = source
    request.side_effect = TimeoutError("目录超时")
    rows = AkShareAdapter()._fetch_hk_financial("00700.HK")
    request.assert_called_once()
    assert [r.revenue for r in rows] == [100, 120]
    assert rows[-1].operating_cash_flow == 120
    assert all(r.report_currency == "" for r in rows)
    assert "目录" in caplog.text and "超时" in caplog.text


def test_metadata_enables_disclosure_without_changing_score_status_or_risks(source):
    frames, request = source
    for row in frames[0] + frames[2]:
        row["CURRENCY_CODE"] = "CNY"
    rows = AkShareAdapter()._fetch_hk_financial("00700.HK")
    known = FinancialEnricher().enrich(AnalysisContext(symbol="00700.HK", name="腾讯控股", market="hk",
                                                    financial_data=rows))
    unknown = known.model_copy(deep=True)
    unknown.financial_data = [r.model_copy(update={"report_currency": "",
                             "reporting_basis": "东方财富港股财报-报告期"}) for r in rows]
    analyzer = FinancialAnalyzer()
    before = analyzer.analyze(unknown, {"meta": {"strategy_key": "GeneralScorer"}})
    after = analyzer.analyze(known, {"meta": {"strategy_key": "GeneralScorer"}})
    assert (after.score, after.score_detail, after.risk_flags, after.status) == (
        before.score, before.score_detail, before.risk_flags, before.status)
    assert after.metrics["operating_trends"] and not before.metrics["operating_trends"]
    assert "金额币种：CNY" in after.metrics["amount_basis"]
    assert "金额币种未核实" in before.metrics["amount_basis"]
    request.assert_called_once()

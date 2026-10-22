"""真实三标的财报回放：银行映射、金额币种证据与旧缓存保护。"""
import json
from datetime import date
from pathlib import Path

import pytest

from analysis.financial import FinancialAnalyzer
from analysis.financial_trends import build_financial_trends
from data.akshare import AkShareAdapter
from data.enrichers.financial_enricher import FinancialEnricher
from data.schemas import AnalysisContext, FinancialData
from report.scoring import build_report


def replay(mocker, code, *, explicit_currency="", directory_currency=None):
    path = Path(__file__).parents[1] / "resources" / "hk" / f"eastmoney-{code}-financial-20261022.json"
    sample = json.loads(path.read_text(encoding="utf-8"))
    if explicit_currency:
        for row in sample["income"] + sample["cash"]:
            row["CURRENCY_CODE"] = explicit_currency
    if directory_currency:
        for row in sample["directory"]:
            row["CURRENCY"] = directory_currency
    requests = mocker.patch("data.akshare.financial_statement", side_effect=[
        sample["income"], sample["balance"], sample["cash"]])
    directory = mocker.patch("data.akshare.financial_report_directory", return_value=sample["directory"])
    mocker.patch("requests.get", side_effect=AssertionError("离线回放禁止网络请求"))
    rows = AkShareAdapter()._fetch_hk_financial(f"{code}.HK")
    assert requests.call_count == 3
    directory.assert_called_once_with(code)
    return rows


@pytest.mark.parametrize("code", ["00700", "09988", "00005"])
def test_real_directory_currency_is_not_amount_evidence(mocker, code):
    rows = replay(mocker, code)
    latest = max(rows, key=lambda row: row.fiscal_quarter)
    assert latest.report_currency == ""
    assert latest.amount_currency_verified is False
    assert latest.disclosure_currency == ("USD" if code == "00005" else "CNY")
    assert latest.financial_selection_status == "complete"
    assert build_financial_trends(rows, f"{code}.HK")["latest_yoy"] == {}
    ctx = FinancialEnricher().enrich(AnalysisContext(symbol=f"{code}.HK", name="验收", market="hk", financial_data=rows))
    result = FinancialAnalyzer().analyze(ctx)
    assert "未核实" in result.metrics["amount_basis"]
    assert "未换汇" not in result.metrics["amount_basis"]
    assert "revenue_growth_yoy" not in result.metrics
    assert "profit_growth_yoy" not in result.metrics
    text = build_report(ctx.symbol, ctx.name, [result], {}, ctx, no_llm=True)
    assert "币种未核实" in text
    assert "财务金额保持原始财报原币口径" not in text
    assert "未换汇" not in text
    assert "现金流同比" not in text


def test_bank_uses_its_own_codes_and_preserves_raw_amounts(mocker):
    latest = max(replay(mocker, "00005"), key=lambda row: row.fiscal_quarter)
    assert latest.revenue == 241030940100
    assert latest.net_profit == 99616223400
    assert latest.basic_eps == 5.789265
    assert latest.total_assets == 23416970754900
    assert latest.total_equity == 1342298982900
    assert latest.common_equity == 1339581433800
    assert latest.operating_cash_flow == 556872805800
    assert latest.gross_margin is None
    assert latest.revenue_basis == "银行：经营收入总额"
    assert latest.period_start == date(2026, 1, 1)


@pytest.mark.parametrize(("code", "revenue", "profit", "start"), [
    ("00700", 396431000000, 114115000000, date(2026, 1, 1)),
    ("09988", 268953000000, 10614000000, date(2026, 4, 1)),
])
def test_general_company_values_and_periods_unchanged(mocker, code, revenue, profit, start):
    latest = max(replay(mocker, code), key=lambda row: row.fiscal_quarter)
    assert (latest.revenue, latest.net_profit, latest.period_start) == (revenue, profit, start)


def test_explicit_statement_currency_is_not_replaced_by_disclosure_currency(mocker):
    rows = replay(mocker, "00005", explicit_currency="CNY", directory_currency="USD")
    assert all(row.report_currency == "CNY" and row.amount_currency_verified for row in rows)
    assert all(row.disclosure_currency == "USD" for row in rows)
    assert build_financial_trends(rows, "00005.HK")["latest_yoy"]
    restored = FinancialData.model_validate_json(rows[-1].model_dump_json())
    assert restored.report_currency == "CNY" and restored.amount_currency_verified


def test_legacy_hk_cache_does_not_restore_unverified_currency():
    row = FinancialData.model_validate({"symbol": "00005.HK", "fiscal_quarter": "2026-06-30",
        "report_currency": "USD", "operating_cash_flow": 556872805800,
        "gross_margin": 0.5, "gross_margin_basis": "计算值：同期间披露毛利÷营运收入；未换汇",
        "reporting_basis": "东方财富港股财报-报告期-国际会计准则"})
    assert row.report_currency == ""
    assert row.operating_cash_flow == 556872805800
    assert not row.amount_currency_verified
    assert "未换汇" not in row.gross_margin_basis
    ctx = FinancialEnricher().enrich(AnalysisContext(symbol=row.symbol, name="旧缓存", market="hk", financial_data=[row]))
    result = FinancialAnalyzer().analyze(ctx)
    assert "未换汇" not in build_report(ctx.symbol, ctx.name, [result], {}, ctx, no_llm=True)


def test_a_share_currency_remains_compatible():
    row = FinancialData(symbol="600519", fiscal_quarter=date(2026, 6, 30), report_currency="CNY")
    assert row.report_currency == "CNY"


def test_bank_does_not_fill_missing_field_from_general_template(mocker):
    sample = json.loads((Path(__file__).parents[1] / "resources/hk/eastmoney-00005-financial-20261022.json").read_text(encoding="utf-8"))
    sample["balance"] = [{**row, "STD_ITEM_CODE": "004009999", "AMOUNT": 123}
                         for row in sample["balance"] if row["STD_ITEM_CODE"] == "001001999"]
    mocker.patch("data.akshare.financial_statement", side_effect=[sample["income"], sample["balance"], sample["cash"]])
    mocker.patch("data.akshare.financial_report_directory", return_value=sample["directory"])
    rows = AkShareAdapter()._fetch_hk_financial("00005.HK")
    assert all(row.total_assets is None and row.net_profit is not None for row in rows)


def test_conflicting_income_templates_are_not_combined(mocker, caplog):
    common = {"SECUCODE": "00005.HK", "SECURITY_CODE": "00005", "REPORT_DATE": "2026-06-30"}
    mocker.patch("data.akshare.financial_statement", side_effect=[[
        {**common, "STD_ITEM_CODE": "001025002", "AMOUNT": 1},
        {**common, "STD_ITEM_CODE": "004001001", "AMOUNT": 2},
    ], [], []])
    mocker.patch("data.akshare.financial_report_directory", return_value=[])
    assert AkShareAdapter()._fetch_hk_financial("00005.HK") == []
    assert "模板冲突" in caplog.text

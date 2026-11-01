"""港股毛利率复用原利润表，异常科目不得污染既有金额。"""
import json
from datetime import date
from pathlib import Path

import pytest

from analysis.financial import FinancialAnalyzer
from analysis.financial_trends import build_financial_trends
from data.akshare import AkShareAdapter
from data.hk_financial_metrics import gross_margins
from data.schemas import AnalysisContext, FinancialData


def subject(code, amount, **extra):
    return {"SECUCODE": "00700.HK", "SECURITY_CODE": "00700", "REPORT_DATE": "2026-06-30",
            "START_DATE": "2026-01-01", "STD_ITEM_CODE": code, "AMOUNT": amount, **extra}


def collect(mocker, rows):
    request = mocker.patch("data.akshare.financial_statement", side_effect=[rows, [], []])
    directory = mocker.patch("data.akshare.financial_report_directory", return_value=[])
    data = AkShareAdapter()._fetch_hk_financial("00700.HK")
    assert request.call_count == 3
    directory.assert_called_once_with("00700")
    return data


def test_real_tencent_uses_operating_revenue_not_turnover(mocker):
    path = Path(__file__).parents[1] / "resources/data/eastmoney-00700-income-report.json"
    rows = collect(mocker, json.loads(path.read_text(encoding="utf-8")))
    latest = rows[-1]
    assert latest.revenue == 396431000000
    assert latest.net_profit == 114115000000
    assert latest.gross_margin == pytest.approx(229698000000 / 401243000000)
    assert latest.gross_margin != pytest.approx(229698000000 / latest.revenue)
    assert "计算值" in latest.gross_margin_basis
    assert "毛利÷营运收入" in latest.gross_margin_basis
    assert "其他营业收入" in latest.gross_margin_basis
    assert all(row.gross_margin is not None for row in rows)
    restored = FinancialData.model_validate_json(latest.model_dump_json())
    assert restored.gross_margin_basis == latest.gross_margin_basis
    result = FinancialAnalyzer().analyze(AnalysisContext(symbol="00700.HK", name="腾讯", market="hk", financial_data=rows))
    assert result.metrics["gross_margin_basis"] == latest.gross_margin_basis
    assert result.score is None and not result.risk_flags


@pytest.mark.parametrize("gross", [0, -10, 60, 120])
def test_finite_zero_negative_and_large_gross_profit_are_preserved(mocker, gross):
    rows = [subject("004001001", 90), subject("004007999", gross), subject("004001999", 100)]
    latest = collect(mocker, rows)[0]
    assert latest.gross_margin == gross / 100
    assert latest.revenue == 90


@pytest.mark.parametrize("bad", [None, "--", float("nan"), float("inf"), float("-inf"), True])
@pytest.mark.parametrize("code", ["004007999", "004001999"])
def test_bad_subject_only_disables_margin(mocker, code, bad):
    rows = [subject("004001001", 90), subject("004025002", 10),
            subject("004007999", 60), subject("004001999", 100)]
    for row in rows:
        if row["STD_ITEM_CODE"] == code:
            row["AMOUNT"] = bad
    latest = collect(mocker, rows)[0]
    assert latest.gross_margin is None and not latest.gross_margin_basis
    assert latest.revenue == 90 and latest.net_profit == 10


@pytest.mark.parametrize("denominator", [0, -100, 1e-310])
def test_nonpositive_or_overflowing_denominator_is_not_usable(mocker, denominator):
    latest = collect(mocker, [subject("004001001", 90), subject("004007999", 60),
                              subject("004001999", denominator)])[0]
    assert latest.gross_margin is None


@pytest.mark.parametrize("extra", [
    {"START_DATE": None}, {"START_DATE": "bad"}, {"START_DATE": "2026-04-01"},
    {"REPORT_DATE": "2025-06-30"}, {"SECUCODE": "00005.HK"}, {"SECURITY_CODE": "00005"},
    {"CURRENCY": "港币"}, {"UNIT": "万元"},
])
def test_mismatched_subject_context_cannot_be_combined(mocker, extra):
    rows = [subject("004001001", 90, CURRENCY="人民币", UNIT="元"),
            subject("004007999", 60, CURRENCY="人民币", UNIT="元"),
            subject("004001999", 100, **{"CURRENCY": "人民币", "UNIT": "元", **extra})]
    assert all(row.gross_margin is None for row in collect(mocker, rows))


@pytest.mark.parametrize("value,expected", [(100, .6), (101, None), (None, None)])
def test_duplicate_subjects_require_same_valid_amount(mocker, value, expected):
    rows = [subject("004001001", 90), subject("004007999", 60),
            subject("004001999", 100), subject("004001999", value)]
    latest = collect(mocker, rows)[0]
    assert latest.gross_margin == expected
    assert latest.revenue == 90


@pytest.mark.parametrize("missing", ["004001999", "004007999", "both"])
def test_missing_subjects_never_use_turnover_or_derive_gross_profit(mocker, missing):
    rows = [subject("004001001", 90), subject("004005001", 40), subject("004025002", 10)]
    if missing != "004007999" and missing != "both":
        rows.append(subject("004007999", 60))
    if missing != "004001999" and missing != "both":
        rows.append(subject("004001999", 100))
    assert collect(mocker, rows)[0].gross_margin is None


def test_margin_enters_existing_trend_candidates_without_extra_highlights(mocker):
    records = []
    for year, gross in [(2025, 50), (2026, 60)]:
        for code, amount in [("004001001", 90), ("004007999", gross), ("004001999", 100)]:
            records.append(subject(code, amount, REPORT_DATE=f"{year}-06-30", START_DATE=f"{year}-01-01"))
    rows = collect(mocker, records)
    trends = build_financial_trends(rows, "00700.HK")
    assert 1 <= len(trends["highlights"]) <= 3
    assert any("毛利率" in text and "10.00 个百分点" in text for text in trends["highlights"])
    old = FinancialData(symbol="00700.HK", fiscal_quarter=date(2026, 6, 30))
    assert old.gross_margin is None and old.gross_margin_basis == ""


def test_actual_noncalendar_quarter_and_absent_cost_subject_are_supported(mocker):
    rows = [subject("004001001", 100, START_DATE="2026-04-01"),
            subject("004001999", 100, START_DATE="2026-04-01"),
            subject("004007999", 30, START_DATE="2026-04-01")]
    latest = collect(mocker, rows)[0]
    assert latest.gross_margin == .3
    assert latest.period_start == date(2026, 4, 1)


@pytest.mark.parametrize("extra", [
    {"CURRENCY": "--"}, {"CURRENCY": "人民币", "CURRENCY_CODE": "HKD"},
    {"START_DATE": "2026-07-01"}, {"REPORT_DATE": "bad"},
])
def test_invalid_metadata_and_cross_field_currency_conflicts_disable_margin(mocker, extra):
    rows = [subject("004001001", 90), subject("004007999", 60), subject("004001999", 100, **extra)]
    assert all(row.gross_margin is None for row in collect(mocker, rows))


@pytest.mark.parametrize("extra", [{"AMOUNT_UNIT": "万元"}, {"UNIT": "元", "AMOUNT_UNIT": "万元"},
                                  {"UNIT_CODE": "10000"}])
def test_explicit_units_in_different_fields_cannot_hide_conflicts(mocker, extra):
    rows = [subject("004001001", 90), subject("004007999", 60, UNIT="元"),
            subject("004001999", 100, **extra)]
    assert collect(mocker, rows)[0].gross_margin is None


@pytest.mark.parametrize("code,expected,start", [
    ("01810", 43419038000 / 208063227000, "2026-01-01"),
    ("09988", 102857000000 / 268953000000, "2026-04-01"),
    ("00005", None, None),
])
def test_trimmed_real_subject_samples(code, expected, start):
    path = Path(__file__).parents[1] / f"resources/data/eastmoney-{code}-income-gross-margin.json"
    sample = json.loads(path.read_text(encoding="utf-8"))
    margins = gross_margins(sample["subjects"], f"{code}.HK")
    assert sample["success"] and sample["pages"] == 1
    if expected is None:
        assert sample["count"] == 32 and sample["subjects"] == [] and margins == {}
    else:
        assert margins[date(2026, 6, 30)] == pytest.approx(expected)
        assert all(row["START_DATE"].startswith(start) for row in sample["subjects"])

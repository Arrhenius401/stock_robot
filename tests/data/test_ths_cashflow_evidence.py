"""同花顺直接现金流与毛利率口径必须能作为财务评分证据。"""
from datetime import date

import pandas as pd
import pytest

from analysis.config_loader import ConfigLoader
from analysis.financial import FinancialAnalyzer
from analysis.financial_scoring import cashflow_compatible, gross_margin_periods
from analysis.scorers.general import GeneralScorer
from data.akshare import AkShareAdapter
from data.financial_selection import FinancialPackage
from data.schemas import AnalysisContext, DimensionSufficiency, SufficiencyLevel

PERIODS = ["2023-06-30", "2024-06-30", "2025-06-30", "2026-06-30"]
REPORTED_OCF = ["10.0亿", "366.22亿", "131.19亿", "706.91亿"]
GROSS_MARGIN_BASIS = "同花顺财务摘要-销售毛利率（摘要口径，未重算）"


@pytest.fixture(autouse=True)
def isolate_financial_backups(monkeypatch):
    """备用整包不隐式触网；主备选择由既有专项测试覆盖。"""
    monkeypatch.setattr("data.a_financial_sources.fetch_sina_financial",
                        lambda _: FinancialPackage("新浪财报", [], {}))
    monkeypatch.setattr("data.a_financial_sources.fetch_eastmoney_financial",
                        lambda _: FinancialPackage("东方财富财报", [], {}))


def _summary() -> pd.DataFrame:
    return pd.DataFrame({
        "报告期": PERIODS,
        "营业总收入": ["100.0亿", "834.51亿", "910.94亿", "922.78亿"],
        "净利润": ["10.0亿", "416.96亿", "454.03亿", "445.17亿"],
        "扣非净利润": ["9.0亿", "416.70亿", "453.90亿", "444.64亿"],
        "净资产收益率": ["1.0", "30.0", "31.0", "32.0"],
        "销售毛利率": ["91.0%", "91.76%", "91.30%", "89.56%"],
        "每股经营现金流": ["1.0", "20.0", "10.0", "56.55"],
        "基本每股收益": ["0.1", "10.0", "10.0", "10.0"],
    })


def _balance() -> pd.DataFrame:
    dates = PERIODS * 2
    return pd.DataFrame({
        "report_date": dates,
        "metric_name": ["assets_total"] * 4 + ["holder_equity_total"] * 4,
        "value": ["3000亿"] * 4 + ["2500亿"] * 4,
    })


def _fetch(mocker, *, summary: pd.DataFrame | None = None,
           cash_frame: pd.DataFrame | None = None, cash_side_effect=None):
    mocker.patch("akshare.stock_financial_abstract_ths",
                 return_value=summary if summary is not None else _summary())
    mocker.patch("akshare.stock_financial_debt_new_ths", return_value=_balance())
    mocker.patch("akshare.stock_financial_debt_ths", return_value=pd.DataFrame())
    if cash_side_effect is not None:
        mocker.patch("akshare.stock_financial_cash_ths", side_effect=cash_side_effect)
    else:
        frame = cash_frame if cash_frame is not None else pd.DataFrame({
            "报告期": PERIODS,
            "*经营活动产生的现金流量净额": REPORTED_OCF,
        })
        mocker.patch("akshare.stock_financial_cash_ths", return_value=frame)
    return AkShareAdapter()._fetch_financial("600519")


def _context(rows) -> AnalysisContext:
    ctx = AnalysisContext(symbol="600519", name="贵州茅台", financial_data=rows)
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT)
    ctx.financial_scoring_facts = {"annual_roe": {
        "value": 0.3253, "period_start": "2025-01-01", "period_end": "2025-12-31",
        "source": "同花顺财报", "basis": "加权ROE", "reason": "",
    }}
    return ctx


def test_ths_reported_cashflow_and_gross_margin_basis_reach_scorer(mocker):
    rows = _fetch(mocker)
    by_period = {row.fiscal_quarter.isoformat(): row for row in rows}
    assert by_period["2023-06-30"].operating_cash_flow == pytest.approx(10.0e8)
    assert by_period["2024-06-30"].operating_cash_flow == pytest.approx(366.22e8)
    assert by_period["2025-06-30"].operating_cash_flow == pytest.approx(131.19e8)
    latest = by_period["2026-06-30"]
    assert latest.operating_cash_flow == pytest.approx(706.91e8)
    assert latest.cash_flow_basis == "reported"
    assert latest.cash_flow_period_start == date(2026, 1, 1)
    assert latest.gross_margin_basis == GROSS_MARGIN_BASIS
    assert cashflow_compatible(latest)
    assert len(gross_margin_periods(rows)) == 4

    ctx = _context(rows)
    config = ConfigLoader().load("机械设备")
    score, detail, _ = GeneralScorer(config, {}).score_financial(ctx)
    assert score is not None
    assert "经营现金流/净利润" in detail and "毛利率稳定性" in detail
    assert "直接披露" in detail and "reported" not in detail
    assert FinancialAnalyzer().analyze(ctx, config).status == "ok"


def test_cashflow_fetch_failure_keeps_estimated_value_unscored(mocker):
    rows = _fetch(mocker, cash_side_effect=ConnectionError("同花顺现金流不可用"))
    latest = max(rows, key=lambda row: row.fiscal_quarter)
    assert latest.cash_flow_basis == "estimated"
    assert latest.cash_flow_period_start is None
    assert not cashflow_compatible(latest)

    ctx = _context(rows)
    config = ConfigLoader().load("机械设备")
    scoring = GeneralScorer(config, {})
    scoring.score_financial(ctx)
    assert scoring.financial_score_basis["available_max"] < scoring.financial_score_basis["eligible_max"]
    assert FinancialAnalyzer().analyze(ctx, config).status == "partial"


def test_reported_cashflow_requires_cny_and_period_evidence(mocker):
    summary = _summary()
    summary["币种"] = ["USD"] * 4
    rows = _fetch(mocker, summary=summary)
    latest = max(rows, key=lambda row: row.fiscal_quarter)
    assert latest.report_currency == ""
    assert latest.operating_cash_flow == pytest.approx(706.91e8)
    assert latest.cash_flow_basis == "unknown"
    assert latest.cash_flow_period_start is None
    assert not cashflow_compatible(latest)


def test_conflicting_duplicate_cashflow_date_is_not_scored(mocker):
    cash = pd.DataFrame({
        "报告期": PERIODS + ["2026-06-30"],
        "*经营活动产生的现金流量净额": REPORTED_OCF + ["999.99亿"],
    })
    rows = _fetch(mocker, cash_frame=cash)
    latest = max(rows, key=lambda row: row.fiscal_quarter)
    assert latest.cash_flow_basis == "estimated"
    assert latest.cash_flow_period_start is None
    assert not cashflow_compatible(latest)


def test_missing_cashflow_column_keeps_estimated(mocker):
    rows = _fetch(mocker, cash_frame=pd.DataFrame({"报告期": PERIODS}))
    latest = max(rows, key=lambda row: row.fiscal_quarter)
    assert latest.cash_flow_basis == "estimated"
    assert latest.cash_flow_period_start is None
    assert not cashflow_compatible(latest)


def test_direct_cashflow_without_standard_period_is_not_reported(mocker):
    summary = _summary()
    summary.loc[3, "报告期"] = "2026-05-31"
    cash = pd.DataFrame({
        "报告期": PERIODS[:-1] + ["2026-05-31"],
        "*经营活动产生的现金流量净额": REPORTED_OCF,
    })
    rows = _fetch(mocker, summary=summary, cash_frame=cash)
    latest = max(rows, key=lambda row: row.fiscal_quarter)
    assert latest.fiscal_quarter == date(2026, 5, 31)
    assert latest.operating_cash_flow == pytest.approx(706.91e8)
    assert latest.cash_flow_basis == "unknown"
    assert latest.cash_flow_period_start is None
    assert not cashflow_compatible(latest)


def test_complete_evidence_allows_core_signal_gate(mocker):
    from data.schemas import AnalysisResult
    from report.quality import build_data_quality

    rows = _fetch(mocker)
    ctx = _context(rows)
    ctx.sufficiency.price = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT)
    ctx.sufficiency.valuation = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT)
    config = ConfigLoader().load("机械设备")
    financial = FinancialAnalyzer().analyze(ctx, config)
    results = [
        financial,
        AnalysisResult(dimension="technical", status="ok", score=8, summary="技术"),
        AnalysisResult(dimension="valuation", status="ok", score=7, summary="估值"),
    ]
    quality = build_data_quality(ctx, results)
    assert financial.status == "ok"
    assert quality["signal_allowed"]

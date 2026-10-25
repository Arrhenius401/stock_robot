"""新五维汇总的方向、缺失、有效零分和版本边界。"""
from typing import Literal

import pytest

from data.schemas import AnalysisResult
from report.scoring import compute_score_summary


def result(dimension, score, *, status: Literal["ok", "partial", "unavailable"] = "ok", risks=None):
    metrics = ({"sentiment_scoring_version": "risk-v1", "assessment_status": "complete",
                "risk_score": score} if dimension == "sentiment" else {})
    return AnalysisResult(dimension=dimension, status=status, score=score, summary="测试",
                          risk_flags=risks or [], metrics=metrics)


def core_results():
    return [result("financial", 8), result("technical", 6),
            result("valuation", 7), result("industry", 7.2)]


@pytest.mark.parametrize(("risk", "expected"), [(0, 7.3), (2, 7.2), (5, 7.0), (10, 6.8)])
def test_sentiment_is_reversed_with_five_percent_weight(risk, expected):
    summary = compute_score_summary([*core_results(), result("sentiment", risk)])
    assert summary.final_score == expected
    row = next(row for row in summary.score_rows if row["label"] == "舆情风险")
    assert float(row["score"]) == 10 - risk
    assert row["raw_score"] == risk
    assert row["direction"] == "risk"
    assert row["weight"] == "5%"


def test_missing_sentiment_renormalizes_core_and_legacy_score_is_not_risk():
    expected = round((8 * 30 + 6 * 20 + 7 * 25 + 7.2 * 20) / 95, 1)
    assert compute_score_summary(core_results()).final_score == expected
    legacy = result("sentiment", 10).model_copy(update={"metrics": {}})
    assert compute_score_summary([*core_results(), legacy]).final_score == expected


def test_risk_flags_are_visible_but_do_not_deduct():
    values = core_results()
    values[0].risk_flags = ["high_debt", "high_debt"]
    values[1].risk_flags = ["bearish_ma"]
    values.append(result("sentiment", 2, risks=["major_negative_news"]))
    summary = compute_score_summary(values)
    assert summary.final_score == 7.2
    assert summary.risk_deduction == 0
    assert summary.risk_flags == ["high_debt", "bearish_ma", "major_negative_news"]


def test_no_core_score_means_no_composite_even_with_zero_risk():
    assert compute_score_summary([result("sentiment", 0)]).final_score is None
    assert compute_score_summary([result("industry", 0), result("sentiment", 0)]).final_score == 2


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 10.1])
def test_invalid_core_values_are_skipped(bad):
    summary = compute_score_summary([result("financial", bad), result("technical", 0)])
    assert summary.final_score == 0


def test_partial_status_does_not_multiply_a_valid_score():
    assert compute_score_summary([result("financial", 8, status="partial")]).final_score == 8


def test_actual_scorers_keep_precision_until_final_composite_display():
    from datetime import date, timedelta

    from analysis.config_loader import ConfigLoader
    from analysis.scorers.general import GeneralScorer
    from data.schemas import AnalysisContext, FinancialData, PriceData

    ctx = AnalysisContext(symbol="000333", name="样例", financial_data=[FinancialData(
        symbol="000333", fiscal_quarter=date(2025, 12, 31), financial_source="真实财报")],
        financial_scoring_facts={"annual_roe": {"value": 0.05, "period_start": "2025-01-01",
            "period_end": "2025-12-31", "source": "真实财报", "basis": "披露ROE", "reason": ""}},
        price_data=[PriceData(symbol="000333", trade_date=date(2026, 1, 1) + timedelta(days=i),
            open=10+i, close=10+i, high=11+i, low=9+i, volume=100) for i in range(20)])
    scorer = GeneralScorer(ConfigLoader().load("机械设备"), {})
    financial = scorer.score_financial(ctx)[0]
    technical = scorer.score_technical(ctx)[0]
    assert financial == pytest.approx(10 / 3)
    assert technical == pytest.approx(40 / 7)
    summary = compute_score_summary([result("financial", financial), result("technical", technical),
        result("valuation", 5), result("industry", 5.8), result("sentiment", 10)])
    assert summary.final_score == 4.6

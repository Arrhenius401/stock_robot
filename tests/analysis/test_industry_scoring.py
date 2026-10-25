"""行业经营分只消费已生成的同期比较证据。"""
from copy import deepcopy

import pytest

from analysis.industry_scoring import score_industry_comparison


def comparison():
    def period(start, end, values):
        return {"start": start, "end": end, "basis": "累计；加权ROE", "reason": "",
                "metrics": [{"key": key, "target": 12, "median": 10,
                             "percentile": percentile, "sample_count": 5, "status": "ok"}
                            for key, percentile in zip(("revenue_growth_yoy", "roe", "net_margin"), values, strict=True)]}
    return {"version": 1, "periods": {
        "annual": period("2025-01-01", "2025-12-31", (80, 70, 60)),
        "latest": period("2026-01-01", "2026-06-30", (20, 30, 40)),
    }}


def test_metric_and_report_period_weights():
    data = comparison()
    original = deepcopy(data)
    score, detail, evidence = score_industry_comparison(data)
    assert score == pytest.approx(7.2 * 0.4 + 2.8 * 0.6)
    assert evidence["periods"]["annual"]["score"] == pytest.approx(7.2)
    assert evidence["period_weights"] == {"annual": 0.4, "latest": 0.6}
    assert "年度" in detail and "最新" in detail
    assert data == original


def test_inapplicable_margin_and_missing_period_are_renormalized():
    data = comparison()
    annual = data["periods"]["annual"]
    annual["metrics"][2].update(status="insufficient", sample_count=0, percentile=None)
    data["periods"]["latest"]["reason"] = "未取得目标最新期"
    score, detail, evidence = score_industry_comparison(data)
    assert score == pytest.approx(7.5)
    assert evidence["period_weights"] == {"annual": 1.0}
    assert "最新" in detail


@pytest.mark.parametrize("bad", [None, float("nan"), -1, 101])
def test_two_valid_metrics_and_five_peers_are_required(bad):
    data = comparison()
    for period in data["periods"].values():
        period["metrics"][1]["percentile"] = bad
        period["metrics"][2]["sample_count"] = 4
    assert score_industry_comparison(data)[0] is None


def test_valid_zero_is_not_missing_and_same_report_counts_once():
    data = comparison()
    for metric in data["periods"]["annual"]["metrics"]:
        metric["percentile"] = 0
    data["periods"]["latest"] = deepcopy(data["periods"]["annual"])
    score, _, evidence = score_industry_comparison(data)
    assert score == 0
    assert sum(evidence["period_weights"].values()) == 1
    assert len(evidence["period_weights"]) == 1


def test_unknown_period_and_future_report_cannot_score():
    data = comparison()
    data["periods"]["annual"]["basis"] = ""
    data["periods"]["latest"]["end"] = "2099-06-30"
    assert score_industry_comparison(data)[0] is None

"""评分计算与报告组装测试"""
from datetime import date
from typing import Literal

from data.schemas import AnalysisContext, PriceData
from report.scoring import build_report, compute_price_info, compute_score_summary


def _result(dimension, score,
            status: Literal["ok", "partial", "unavailable"] = "ok",
            risk_flags=None, score_detail=""):
    from data.schemas import AnalysisResult
    return AnalysisResult(
        dimension=dimension, status=status, summary=f"{dimension} 摘要",
        score=score, score_detail=score_detail,
        risk_flags=risk_flags or [],
    )


def _ctx(price_data=None):
    return AnalysisContext(symbol="000001", name="测试股票", price_data=price_data)


def _price(high, low, close):
    return PriceData(symbol="000001", trade_date=date(2026, 1, 2),
                     open=5.0, high=high, low=low, close=close, volume=1000)


class TestComputeScoreSummary:
    def test_weighted_base_score(self):
        results = [
            _result("financial", 8.0),
            _result("technical", 6.0),
            _result("valuation", 4.0),
            _result("industry", 9.0),
        ]
        summary = compute_score_summary(results)
        # 8.0*0.30 + 6.0*0.20 + 4.0*0.25 + 9.0*0.25 = 6.85 → round(…, 1) = 6.8
        assert summary.base_score == 6.8
        assert summary.risk_deduction == 0
        assert summary.final_score == 6.8

    def test_risk_flags_deduct_capped_at_10(self):
        flags = [f"风险{i}" for i in range(12)]
        results = [
            _result("financial", 8.0, risk_flags=flags),
        ]
        summary = compute_score_summary(results)
        assert summary.risk_deduction == 10
        assert summary.final_score == max(0, summary.base_score - 10)
        assert len(summary.risk_flags) == 12

    def test_missing_dimension_not_scored(self):
        results = [_result("financial", 7.0)]
        summary = compute_score_summary(results)
        assert summary.base_score == 7.0  # 仅财务维度：7.0*0.30/0.30
        assert summary.score_rows[0]["label"] == "财务健康"
        assert summary.score_rows[0]["score"] == "7.0"
        assert summary.score_rows[0]["weight"] == "30%"

    def test_none_score_shown_as_na(self):
        results = [_result("financial", None)]
        summary = compute_score_summary(results)
        assert summary.score_rows[0]["score"] == "N/A"


class TestComputePriceInfo:
    def test_price_position(self):
        ctx = _ctx(price_data=[_price(10.0, 2.0, 4.0), _price(12.0, 3.0, 6.0)])
        info = compute_price_info(ctx)
        assert info["year_high"] == 12.0
        assert info["year_low"] == 2.0
        assert info["latest_price"] == 6.0
        assert info["price_position"] == "40%"

    def test_no_price_data(self):
        ctx = _ctx()
        info = compute_price_info(ctx)
        assert info["year_high"] is None
        assert info["price_position"] == "暂无"


class TestBuildReport:
    def test_builds_report_with_scores(self, mocker):
        mock_builder_cls = mocker.patch("report.builder.ReportBuilder")
        mock_builder = mock_builder_cls.return_value
        mock_builder.build.return_value = "RENDERED_REPORT"

        results = [_result("financial", 8.0)]
        ctx = _ctx()
        report = build_report("000001", "平安银行", results, {"bulk": "解读"},
                              ctx, no_llm=False)

        assert report == "RENDERED_REPORT"
        call_kwargs = mock_builder.build.call_args.kwargs
        assert call_kwargs["symbol"] == "000001"
        assert call_kwargs["industry"] == "未知"
        assert call_kwargs["base_score"] == 8.0
        assert call_kwargs["final_score"] == 8.0

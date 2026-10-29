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
    from data.schemas import DimensionSufficiency, SufficiencyLevel
    ctx = AnalysisContext(symbol="000001", name="测试股票", price_data=price_data)
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=6)
    return ctx


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
        # 四个有效维度保持30:20:25:20原相对权重。
        assert summary.base_score == 6.7
        assert summary.risk_deduction == 0
        assert summary.final_score == 6.7

    def test_risk_flags_are_displayed_without_extra_deduction(self):
        flags = [f"风险{i}" for i in range(12)]
        results = [
            _result("financial", 8.0, risk_flags=flags),
        ]
        summary = compute_score_summary(results)
        assert summary.risk_deduction == 0
        assert summary.base_score is not None
        assert summary.final_score == 8
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

    def test_change_pct_from_latest_price(self):
        from data.schemas import PriceData
        prices = [_price(10.0, 2.0, 4.0),
                  PriceData(symbol="000001", trade_date=date(2026, 1, 3),
                            open=6.0, high=7.0, low=5.0, close=6.0,
                            volume=1000, change_pct=1.15)]
        info = compute_price_info(_ctx(price_data=prices))
        assert info["change_pct"] == 1.15

    def test_no_price_data(self):
        ctx = _ctx()
        info = compute_price_info(ctx)
        assert info["year_high"] is None
        assert info["price_position"] == "暂无"
        assert info["change_pct"] is None


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
        assert call_kwargs["industry"] == "申万行业待补全（数据源不可用）"
        assert call_kwargs["base_score"] == 8.0
        assert call_kwargs["final_score"] == 8.0

    def test_build_report_prefers_sw_industry(self, mocker):
        mock_builder_cls = mocker.patch("report.builder.ReportBuilder")
        mock_builder = mock_builder_cls.return_value
        mock_builder.build.return_value = "RENDERED_REPORT"
        ctx = AnalysisContext(symbol="000001", name="测试股票", sw_industry="农林牧渔")

        build_report("000001", "平安银行", [_result("financial", 8.0)],
                     {"bulk": "解读"}, ctx)

        assert mock_builder.build.call_args.kwargs["industry"] == "农林牧渔"

    def test_build_report_adds_neutral_fallback_when_llm_missing(self, mocker):
        mock_builder_cls = mocker.patch("report.builder.ReportBuilder")
        mock_builder = mock_builder_cls.return_value
        mock_builder.build.return_value = "RENDERED_REPORT"

        results = [
            _result("financial", 7.0, score_detail="财务稳健"),
            _result("sentiment", None, status="unavailable"),
        ]

        build_report("000001", "平安银行", results, {}, _ctx(), no_llm=False)

        bulk = mock_builder.build.call_args.kwargs["commentary"]["bulk"]
        assert "AI 解读当前不可用" in bulk
        assert "最终综合得分为 7.0/10" in bulk
        assert "舆情风险" in bulk

    def test_build_report_keeps_existing_ai_commentary(self, mocker):
        mock_builder_cls = mocker.patch("report.builder.ReportBuilder")
        mock_builder = mock_builder_cls.return_value
        mock_builder.build.return_value = "RENDERED_REPORT"

        build_report(
            "000001", "平安银行", [_result("financial", 8.0)],
            {"bulk": "真实 AI 解读"}, _ctx(), no_llm=False,
        )

        assert mock_builder.build.call_args.kwargs["commentary"]["bulk"] == "真实 AI 解读"

    def test_build_report_no_llm_adds_quantitative_summary(self, mocker):
        mock_builder_cls = mocker.patch("report.builder.ReportBuilder")
        mock_builder = mock_builder_cls.return_value
        mock_builder.build.return_value = "RENDERED_REPORT"

        build_report("000001", "平安银行", [_result("financial", 8.0)], {}, _ctx(),
                     no_llm=True)

        assert "量化摘要" in mock_builder.build.call_args.kwargs["commentary"]["bulk"]


def test_new_runtime_industry_score_and_risks_enter_summary():
    results = [_result("financial", 8), _result("technical", 6), _result("valuation", 4),
               _result("industry", 10, risk_flags=["industry_weak_margin"]), _result("sentiment", 10)]
    summary = compute_score_summary(results)
    assert summary.base_score == 6.9
    assert summary.final_score == 6.9
    assert summary.risk_deduction == 0
    assert "industry_weak_margin" in summary.risk_flags
    industry = next(row for row in summary.score_rows if row["label"] == "行业对比")
    assert industry["weight"] == "20%"
    assert industry["score"] == "10.0"


def test_negative_equity_risk_is_chinese_and_does_not_deduct_composite():
    from report.formatter import risk_display_name

    assert risk_display_name("negative_equity") == "净资产为负（资不抵债风险）"
    summary = compute_score_summary([_result("financial", 8, risk_flags=["negative_equity"])])
    assert summary.final_score == 8
    assert summary.risk_deduction == 0
    assert summary.risk_flags == ["negative_equity"]

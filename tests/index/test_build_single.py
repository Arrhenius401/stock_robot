"""指数单报告构建器测试"""
import pytest
from datetime import date
from src.index.build_single import IndexReportBuilder
from data.schemas import (
    AnalysisTarget, IndexAnalysisContext, IndexValuationData,
    AnalysisResult, IndexReport
)


@pytest.fixture
def broad_ctx():
    target = AnalysisTarget(
        target_type="index", symbol="000300",
        name="沪深300", market="a-shares", index_style="broad"
    )
    ctx = IndexAnalysisContext(target=target)
    ctx.valuation_data = IndexValuationData(
        symbol="000300", date=date.today(),
        pe_ttm=12.5, pb=1.4, pe_percentile=68.0,
        percentile_lookback_years=5,
        percentile_sample_start=date(2021, 8, 1),
        percentile_sample_end=date(2026, 8, 1),
        valuation_valid=True
    )
    return ctx


@pytest.fixture
def broad_results():
    return [
        AnalysisResult(dimension="index_technical", status="ok",
                       summary="趋势: bull", metrics={"tag": "bull", "latest_close": 4000.0}),
        AnalysisResult(dimension="index_valuation", status="ok",
                       summary="PE 分位 68%", metrics={"tag": "neutral", "pe_ttm": 12.5}),
        AnalysisResult(dimension="index_capital_flow", status="ok",
                       summary="资金面: positive", metrics={"tag": "positive"}),
        AnalysisResult(dimension="index_macro", status="ok",
                       summary="PMI 50.5", metrics={"tag": "neutral", "pmi": 50.5}),
        AnalysisResult(dimension="index_sentiment", status="ok",
                       summary="舆情 30 条", metrics={"tag": "neutral"}),
    ]


class TestIndexReportBuilder:
    def test_build_broad_report(self, broad_ctx, broad_results):
        builder = IndexReportBuilder()
        report = builder.build(broad_ctx, broad_results)
        assert isinstance(report, IndexReport)
        assert report.code == "000300"
        assert report.tag_technical == "bull"
        assert "macro" in report.visible_sections
        assert "capital" in report.visible_sections

    def test_visible_sections_sector(self):
        target = AnalysisTarget(
            target_type="index", symbol="801080",
            name="电子", market="a-shares", index_style="sector"
        )
        ctx = IndexAnalysisContext(target=target)
        results = [
            AnalysisResult(dimension="index_technical", status="partial",
                           summary="shake", metrics={"tag": "shake"}),
            AnalysisResult(dimension="index_valuation", status="partial",
                           summary="NA", metrics={"tag": "invalid"}),
            AnalysisResult(dimension="index_capital_flow", status="ok",
                           summary="neutral", metrics={"tag": "neutral"}),
            AnalysisResult(dimension="index_macro", status="ok",
                           summary="行业不适用", metrics={"tag": "na"}),
            AnalysisResult(dimension="index_sentiment", status="unavailable",
                           summary="暂无舆情", metrics={}),
        ]
        builder = IndexReportBuilder()
        report = builder.build(ctx, results)
        assert "macro" not in report.visible_sections

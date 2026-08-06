"""指数数据模型测试"""
import pytest
from datetime import date
from src.data.schemas import (
    AnalysisTarget, IndexPriceData, IndexValuationData,
    CapitalFlowData, MacroContext, IndexAnalysisContext, IndexReport
)
from src.data.schemas import PriceData, RawSentimentData, EnrichedSentiment, DataSufficiency


class TestAnalysisTarget:
    def test_stock_target_creation(self):
        target = AnalysisTarget(
            target_type="stock", symbol="000001",
            name="平安银行", market="a-shares"
        )
        assert target.target_type == "stock"
        assert target.index_style is None

    def test_index_target_creation(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        assert target.target_type == "index"
        assert target.index_style == "broad"


class TestIndexValuationData:
    def test_default_valuation_valid(self):
        v = IndexValuationData(symbol="000300", date=date.today())
        assert v.valuation_valid is True
        assert v.percentile_lookback_years == 5

    def test_valuation_invalid_when_sample_short(self):
        v = IndexValuationData(
            symbol="000300", date=date.today(),
            valuation_valid=False,
            percentile_sample_start=date(2024, 1, 1),
            percentile_sample_end=date(2026, 8, 1),
        )
        assert v.valuation_valid is False
        assert v.pe_percentile is None


class TestIndexAnalysisContext:
    def test_empty_context(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        assert ctx.price_data == []
        assert ctx.valuation_data is None
        assert ctx.capital_flow is None
        assert ctx.macro is None
        assert ctx.raw_sentiment is None

    def test_context_with_risk_flags(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target, risk_flags=["PE 处于历史高位"])
        assert len(ctx.risk_flags) == 1


class TestIndexReport:
    def test_report_visible_sections_broad(self):
        report = IndexReport(
            code="000300", name="沪深300", date=date.today(),
            overview={}, section_technical={}, section_valuation={},
            section_capital={}, section_macro={}, section_sentiment={},
            tag_technical="bull", tag_valuation="neutral",
            tag_capital="positive", tag_macro="neutral", tag_sentiment="neutral",
            composite_comment="谨慎看多", position_coeff=0.6,
            risk_list=[], visible_sections={"overview", "technical", "valuation",
                                             "capital", "macro", "sentiment"}
        )
        assert "macro" in report.visible_sections

    def test_report_visible_sections_sector_hides_macro(self):
        report = IndexReport(
            code="399006", name="创业板指", date=date.today(),
            overview={}, section_technical={}, section_valuation={},
            section_capital={}, section_macro=None, section_sentiment={},
            tag_technical="shake", tag_valuation="overvalued",
            tag_capital="neutral", tag_macro="na", tag_sentiment="negative",
            composite_comment="中性震荡", position_coeff=0.35,
            risk_list=[],
            visible_sections={"overview", "technical", "valuation", "capital", "sentiment"}
        )
        assert "macro" not in report.visible_sections

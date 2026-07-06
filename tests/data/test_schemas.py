from datetime import date
import pytest
from pydantic import ValidationError
from data.schemas import (
    FinancialData,
    PriceData,
    ValuationData,
    IndustryData,
    NewsData,
    AnalysisResult,
    AnalysisContext,
)


class TestFinancialData:
    def test_valid_financial_data(self):
        d = FinancialData(
            symbol="000001",
            fiscal_quarter=date(2025, 12, 31),
            revenue=45_000_000_000.0,
            net_profit=8_500_000_000.0,
            total_assets=500_000_000_000.0,
            total_equity=45_000_000_000.0,
            operating_cash_flow=12_000_000_000.0,
            roe=0.12,
            gross_margin=0.32,
        )
        assert d.symbol == "000001"
        assert d.roe == 0.12

    def test_optional_fields_accept_none(self):
        d = FinancialData(
            symbol="000001",
            fiscal_quarter=date(2025, 12, 31),
            revenue=45_000_000_000.0,
            net_profit=8_500_000_000.0,
            total_assets=500_000_000_000.0,
            total_equity=45_000_000_000.0,
            operating_cash_flow=12_000_000_000.0,
            roe=None,
            gross_margin=None,
        )
        assert d.roe is None

    def test_missing_required_fields_raises_error(self):
        with pytest.raises(ValidationError):
            FinancialData(symbol="000001")


class TestPriceData:
    def test_valid_price_data(self):
        d = PriceData(
            symbol="000001",
            trade_date=date(2026, 7, 1),
            open=12.50,
            high=12.80,
            low=12.30,
            close=12.65,
            volume=50_000_000,
        )
        assert d.close == 12.65

    def test_negative_price_raises_error(self):
        with pytest.raises(ValidationError):
            PriceData(
                symbol="000001",
                trade_date=date(2026, 7, 1),
                open=-12.50,
                high=12.80,
                low=12.30,
                close=12.65,
                volume=50_000_000,
            )


class TestValuationData:
    def test_valid_valuation_data(self):
        d = ValuationData(
            symbol="000001",
            date=date(2026, 7, 1),
            pe_ttm=7.5,
            pb=0.85,
            ps_ttm=1.2,
        )
        assert d.pe_ttm == 7.5

    def test_optional_valuation_metrics(self):
        d = ValuationData(
            symbol="000001",
            date=date(2026, 7, 1),
            pe_ttm=None,
            pb=None,
            ps_ttm=None,
        )
        assert d.pe_ttm is None


class TestIndustryData:
    def test_valid_industry_data(self):
        d = IndustryData(
            symbol="000001",
            industry="银行",
            sector="金融",
            peers=["600036", "601398", "601939"],
        )
        assert d.industry == "银行"
        assert len(d.peers) == 3

    def test_empty_peers_is_valid(self):
        d = IndustryData(
            symbol="000001",
            industry="综合",
            sector="其他",
            peers=[],
        )
        assert d.peers == []


class TestNewsData:
    def test_valid_news_data(self):
        d = NewsData(
            symbol="000001",
            date=date(2026, 7, 1),
            headlines=["平安银行发布2025年度报告", "平安银行获批设立理财子公司"],
        )
        assert len(d.headlines) == 2

    def test_empty_headlines_is_valid(self):
        d = NewsData(
            symbol="000001",
            date=date(2026, 7, 1),
            headlines=[],
        )
        assert d.headlines == []


class TestAnalysisResult:
    def test_ok_status(self):
        r = AnalysisResult(
            dimension="financial",
            status="ok",
            summary="营收同比增长15%，ROE维持高位",
            metrics={"revenue_growth": 0.15, "roe": 0.12},
        )
        assert r.status == "ok"

    def test_partial_status(self):
        r = AnalysisResult(
            dimension="technical",
            status="partial",
            summary="部分技术指标可用",
            metrics={"ma5": 12.5},
        )
        assert r.status == "partial"

    def test_unavailable_status(self):
        r = AnalysisResult(
            dimension="sentiment",
            status="unavailable",
            summary="舆情数据暂时不可用",
            metrics={},
        )
        assert r.status == "unavailable"

    def test_charts_defaults_to_empty_list(self):
        r = AnalysisResult(
            dimension="financial",
            status="ok",
            summary="测试",
            metrics={},
        )
        assert r.charts == []


class TestAnalysisContext:
    def test_empty_context(self):
        ctx = AnalysisContext(symbol="000001", name="平安银行")
        assert ctx.symbol == "000001"
        assert ctx.financial_data is None
        assert ctx.price_data is None

    def test_populated_context(self):
        ctx = AnalysisContext(
            symbol="000001",
            name="平安银行",
            financial_data=[
                FinancialData(
                    symbol="000001",
                    fiscal_quarter=date(2025, 12, 31),
                    revenue=45_000_000_000.0,
                    net_profit=8_500_000_000.0,
                    total_assets=500_000_000_000.0,
                    total_equity=45_000_000_000.0,
                    operating_cash_flow=12_000_000_000.0,
                    roe=0.12,
                    gross_margin=0.32,
                )
            ],
        )
        assert len(ctx.financial_data) == 1

    def test_to_dict_serializes_correctly(self):
        ctx = AnalysisContext(symbol="000001", name="平安银行")
        d = ctx.model_dump()
        assert d["symbol"] == "000001"
        assert d["financial_data"] is None

"""打分体系单元测试"""
from datetime import date, datetime, timedelta

from data.schemas import (
    AnalysisContext,
    DailyValuationPoint,
    DataSufficiency,
    DimensionSufficiency,
    EnrichedIndustry,
    EnrichedSentiment,
    EnrichedValuation,
    FinancialData,
    PeerComparison,
    PriceData,
    SufficiencyLevel,
    ValuationData,
)


def make_scoring_context():
    """创建带完整充实数据的 AnalysisContext"""
    ctx = AnalysisContext(
        symbol="000001", name="测试银行",
        financial_data=[
            FinancialData(symbol="000001", fiscal_quarter=date(2025, 12, 31),
                          revenue=100e8, net_profit=12e8, deducted_net_profit=11e8,
                          total_assets=500e8, total_equity=50e8,
                          operating_cash_flow=10e8, roe=0.12, gross_margin=0.45),
            FinancialData(symbol="000001", fiscal_quarter=date(2025, 9, 30),
                          revenue=95e8, net_profit=11e8, deducted_net_profit=10e8,
                          total_assets=490e8, total_equity=48e8,
                          operating_cash_flow=9e8, roe=0.11, gross_margin=0.44),
            FinancialData(symbol="000001", fiscal_quarter=date(2025, 6, 30),
                          revenue=90e8, net_profit=10e8, deducted_net_profit=9e8,
                          total_assets=480e8, total_equity=46e8,
                          operating_cash_flow=8e8, roe=0.10, gross_margin=0.43),
            FinancialData(symbol="000001", fiscal_quarter=date(2025, 3, 31),
                          revenue=85e8, net_profit=9e8, deducted_net_profit=8e8,
                          total_assets=470e8, total_equity=44e8,
                          operating_cash_flow=7e8, roe=0.09, gross_margin=0.42),
        ],
        price_data=[
            PriceData(symbol="000001", trade_date=date(2025, 7, 1) + timedelta(days=i),
                      open=10.0, high=10.5, low=9.8, close=10.2, volume=1000000)
            for i in range(120)
        ],
        valuation_data=ValuationData(symbol="000001", date=datetime.now().astimezone().astimezone().date(),
                                     pe_ttm=7.5, pb=0.85, ps_ttm=1.2),
    )
    ctx.sufficiency = DataSufficiency(
        price=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=120, score_weight=1.0),
        financial=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=4, score_weight=1.0),
        valuation=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=150, score_weight=1.0),
        industry=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=10, score_weight=1.0),
        sentiment=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=15, score_weight=1.0),
    )
    ctx.enriched_valuation = EnrichedValuation(
        daily_points=[
            DailyValuationPoint(trade_date=date(2025, 7, 1) + timedelta(days=i),
                                close=10.2, pe=7.0 + i * 0.01, pb=0.85, ps=1.2)
            for i in range(300)
        ],
        pe_percentile=35.0, pb_percentile=28.0, pe_zone="中性",
        pe_median=8.0, pe_high=12.0, pe_low=5.0,
    )
    ctx.enriched_industry = EnrichedIndustry(
        peer_count=10, top_peers=[
            PeerComparison(symbol="600036", name="招商银行", market_cap=10000e8,
                          pe_ttm=9.0, pb=1.2, gross_margin=0.52,
                          net_margin=0.35, roe=0.15),
        ],
        industry_median_pe=7.0, industry_median_pb=0.90,
        industry_median_gross_margin=0.48, industry_median_net_margin=0.30,
        target_pe_premium=7.1, target_market_cap_rank=8,
    )
    ctx.enriched_sentiment = EnrichedSentiment(total_count=15, positive_count=8,
                                                neutral_count=5, negative_count=2)
    return ctx


def _load_config():
    from pathlib import Path

    from analysis.config_loader import ConfigLoader
    config_dir = Path(__file__).parent.parent / "src" / "analysis" / "config"
    return ConfigLoader(config_dir).load("银行")


class TestFinancialScoring:
    def test_roe_sufficient(self):
        from analysis.financial import FinancialAnalyzer
        ctx = make_scoring_context()
        result = FinancialAnalyzer().analyze(ctx, _load_config())
        assert result.score is not None
        assert 0 <= result.score <= 10
        assert "ROE" in result.score_detail

    def test_insufficient_dimension_no_score(self):
        from analysis.financial import FinancialAnalyzer
        ctx = make_scoring_context()
        ctx.sufficiency.financial.level = SufficiencyLevel.INSUFFICIENT
        ctx.sufficiency.financial.score_weight = 0.0
        result = FinancialAnalyzer().analyze(ctx, _load_config())
        assert result.score is None
        assert result.status == "unavailable"


class TestTechnicalScoring:
    def test_sufficient_returns_score(self):
        from analysis.technical import TechnicalAnalyzer
        ctx = make_scoring_context()
        result = TechnicalAnalyzer().analyze(ctx, _load_config())
        assert result.score is not None
        assert 0 <= result.score <= 10

    def test_insufficient_no_score(self):
        from analysis.technical import TechnicalAnalyzer
        ctx = make_scoring_context()
        ctx.sufficiency.price.level = SufficiencyLevel.INSUFFICIENT
        ctx.sufficiency.price.score_weight = 0.0
        result = TechnicalAnalyzer().analyze(ctx, _load_config())
        assert result.score is None

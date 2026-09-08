"""GeneralScorer 单元测试"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from analysis.config_loader import ConfigLoader
from analysis.scorers.general import GeneralScorer, calculate_macd
from data.schemas import (
    AnalysisContext,
    EnrichedIndustry,
    EnrichedSentiment,
    EnrichedValuation,
    FinancialData,
    PriceData,
    SentimentItem,
    ValuationData,
)

CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "src" / "analysis" / "config"


class TestGeneralScorerFinancial:
    @pytest.fixture
    def config(self):
        return ConfigLoader(CONFIG_DIR).load("家用电器")

    @pytest.fixture
    def ctx(self):
        return AnalysisContext(
            symbol="000333", name="美的集团",
            financial_data=[
                FinancialData(symbol="000333", fiscal_quarter=date(2025,12,31),
                              revenue=100e9, net_profit=12e9, total_assets=200e9,
                              total_equity=80e9, operating_cash_flow=15e9,
                              roe=0.15, gross_margin=0.28),
                FinancialData(symbol="000333", fiscal_quarter=date(2025,9,30),
                              revenue=75e9, net_profit=9e9, total_assets=195e9,
                              total_equity=78e9, operating_cash_flow=10e9,
                              roe=0.12, gross_margin=0.27),
            ],
            valuation_data=ValuationData(symbol="000333", date=date(2025,12,31), pe_ttm=18.5, pb=3.2),
        )

    def test_score_returns_tuple(self, config, ctx):
        scorer = GeneralScorer(config, {})
        score, detail, risks = scorer.score_financial(ctx)
        assert isinstance(score, float)
        assert isinstance(detail, str)
        assert isinstance(risks, list)

    def test_roe_15pct_gets_full_score(self, config, ctx):
        scorer = GeneralScorer(config, {})
        score, detail, _ = scorer.score_financial(ctx)
        assert score > 0
        assert "ROE" in detail

    def test_no_financial_data_zero_score(self, config):
        ctx = AnalysisContext(symbol="000333", name="测试")
        scorer = GeneralScorer(config, {})
        score, detail, _ = scorer.score_financial(ctx)
        assert score == 0.0
        assert "数据不可用" in detail


class TestGeneralScorerValuation:
    @pytest.fixture
    def config(self):
        return ConfigLoader(CONFIG_DIR).load("家用电器")

    @pytest.fixture
    def ctx(self):
        return AnalysisContext(
            symbol="000333", name="美的集团",
            valuation_data=ValuationData(symbol="000333", date=date(2025,12,31), pe_ttm=18.5, pb=3.2),
            enriched_valuation=EnrichedValuation(pe_percentile=25.0, pb_percentile=40.0),
            enriched_industry=EnrichedIndustry(target_pe_premium=-10.0),
        )

    def test_score_valuation_returns_tuple(self, config, ctx):
        scorer = GeneralScorer(config, {})
        score, _, _ = scorer.score_valuation(ctx)
        assert isinstance(score, float)


class TestGeneralScorerIndustry:
    @pytest.fixture
    def config(self):
        return ConfigLoader(CONFIG_DIR).load("家用电器")

    def test_no_enriched_industry_returns_base_score(self, config):
        ctx = AnalysisContext(symbol="000333", name="测试")
        scorer = GeneralScorer(config, {})
        score, detail, _ = scorer.score_industry(ctx)
        assert score >= 5.0
        assert "基础分" in detail


class TestGeneralScorerSentiment:
    @pytest.fixture
    def config(self):
        return ConfigLoader(CONFIG_DIR).load("家用电器")

    def test_no_enriched_sentiment_returns_base_score(self, config):
        ctx = AnalysisContext(symbol="000333", name="测试")
        scorer = GeneralScorer(config, {})
        score, _, _ = scorer.score_sentiment(ctx)
        assert score == 5.0

    def test_positive_sentiment_scores_higher(self, config):
        ctx = AnalysisContext(
            symbol="000333", name="测试",
            enriched_sentiment=EnrichedSentiment(
                total_count=15, positive_count=10, neutral_count=3, negative_count=2,
                all_items=[SentimentItem(title="t", summary="s", tendency="positive", severity="minor")],
            ),
        )
        scorer = GeneralScorer(config, {})
        score, _, _ = scorer.score_sentiment(ctx)
        assert score > 5.0


def test_macd_dea_is_not_equal_to_dif_for_non_linear_prices():
    closes = [
        10.0,
        10.4,
        10.2,
        10.8,
        11.1,
        10.9,
        11.6,
        11.4,
        11.9,
        12.2,
        12.0,
        12.7,
        12.9,
        12.6,
        13.4,
        13.1,
        13.7,
        13.9,
        14.2,
        14.0,
        14.6,
        14.9,
        14.7,
        15.3,
        15.1,
        15.8,
        16.0,
        15.7,
        16.4,
        16.1,
        16.8,
        17.0,
        16.7,
        17.3,
        17.1,
        17.8,
        18.0,
        17.6,
        18.3,
        18.1,
    ]

    macd = calculate_macd(closes)
    assert macd.dea != macd.dif


def test_calculate_macd_preserves_ema_start_and_dea_uses_full_dif_series():
    closes = [
        10.0,
        10.4,
        10.2,
        10.8,
        11.1,
        10.9,
        11.6,
        11.4,
        11.9,
        12.2,
        12.0,
        12.7,
        12.9,
        12.6,
        13.4,
        13.1,
        13.7,
        13.9,
        14.2,
        14.0,
        14.6,
        14.9,
        14.7,
        15.3,
        15.1,
        15.8,
        16.0,
        15.7,
        16.4,
        16.1,
        16.8,
        17.0,
        16.7,
        17.3,
        17.1,
        17.8,
        18.0,
        17.6,
        18.3,
        18.1,
    ]

    macd = calculate_macd(closes)

    assert macd.dif == pytest.approx(1.4532989869, rel=1e-9, abs=1e-9)
    assert macd.dea == pytest.approx(1.4959274304, rel=1e-9, abs=1e-9)
    assert macd.bar == pytest.approx(-0.0852568870, rel=1e-9, abs=1e-9)


def test_score_technical_macd_band_is_stable_with_realistic_config():
    config = {
        "technical": {
            "ma_structure": {"enabled": False},
            "volume_price": {"enabled": False},
            "macd": {"enabled": True, "max_score": 3},
        }
    }
    scorer = GeneralScorer(config, {})
    ctx = AnalysisContext(
        symbol="000333",
        name="测试",
        price_data=[
            PriceData(
                symbol="000333",
                trade_date=date(2026, 1, 1) + timedelta(days=offset),
                open=10.0 + offset * 0.2,
                high=10.2 + offset * 0.2,
                low=9.8 + offset * 0.2,
                close=10.0 + offset * 0.2,
                volume=100000 + offset * 1000,
            )
            for offset in range(40)
        ],
    )

    score, detail, _ = scorer.score_technical(ctx)

    assert score == 1.0
    assert "MACD 空头" in detail

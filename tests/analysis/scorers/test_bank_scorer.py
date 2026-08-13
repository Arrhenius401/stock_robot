"""BankScorer 单元测试"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from analysis.config_loader import ConfigLoader
from analysis.scorers.bank import BankScorer
from data.schemas import (
    AnalysisContext,
    EnrichedValuation,
    FinancialData,
    ValuationData,
)

CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "src" / "analysis" / "config"


class TestBankScorer:
    @pytest.fixture
    def config(self):
        return ConfigLoader(CONFIG_DIR).load("银行")

    @pytest.fixture
    def ctx(self):
        return AnalysisContext(
            symbol="000001", name="平安银行", sw_industry="银行", style_category="大金融",
            financial_data=[
                FinancialData(symbol="000001", fiscal_quarter=date(2025, 12, 31),
                              revenue=45e9, net_profit=8.5e9, total_assets=500e9,
                              total_equity=45e9, operating_cash_flow=12e9,
                              roe=0.12, gross_margin=None),
            ],
            valuation_data=ValuationData(symbol="000001", date=date(2025, 12, 31), pe_ttm=6.5, pb=0.8),
            enriched_valuation=EnrichedValuation(pe_percentile=30.0, pb_percentile=15.0),
        )

    def test_pe_disabled_for_bank(self, config, ctx):
        scorer = BankScorer(config, {})
        _, detail, _ = scorer.score_valuation(ctx)
        assert "跳过" in detail or "不适用" in detail or "PE" not in detail

    def test_pb_is_primary_metric(self, config, ctx):
        scorer = BankScorer(config, {})
        score, detail, _ = scorer.score_valuation(ctx)
        assert score > 0
        assert "PB" in detail

    def test_industry_note_not_empty(self, config):
        scorer = BankScorer(config, {})
        note = scorer._industry_note()
        assert len(note) > 0
        assert "PB" in note

    def test_bank_roe_12pct_gets_score(self, config, ctx):
        scorer = BankScorer(config, {})
        score, detail, _ = scorer.score_financial(ctx)
        assert score > 0
        assert "ROE 12.0%" in detail

    def test_bank_gross_margin_skipped(self, config, ctx):
        scorer = BankScorer(config, {})
        _, detail, _ = scorer.score_financial(ctx)
        assert "毛利率" not in detail or "跳过" in detail

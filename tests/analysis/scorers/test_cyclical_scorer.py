"""CyclicalScorer 单元测试"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from analysis.config_loader import ConfigLoader
from analysis.scorers.cyclical import CyclicalScorer
from data.schemas import AnalysisContext, EnrichedIndustry, EnrichedValuation

CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "src" / "analysis" / "config"


class TestCyclicalScorer:
    @pytest.fixture
    def config(self):
        return ConfigLoader(CONFIG_DIR).load("煤炭")

    @pytest.fixture
    def ctx_low_pe(self):
        """周期顶部：PE 低（盈利高）"""
        return AnalysisContext(
            symbol="601088", name="中国神华", sw_industry="煤炭", style_category="周期资源",
            enriched_valuation=EnrichedValuation(pe_percentile=15.0, pb_percentile=30.0),
            enriched_industry=EnrichedIndustry(target_pe_premium=5.0),
        )

    @pytest.fixture
    def ctx_high_pe(self):
        """周期底部：PE 高（盈利低）"""
        return AnalysisContext(
            symbol="601088", name="中国神华", sw_industry="煤炭", style_category="周期资源",
            enriched_valuation=EnrichedValuation(pe_percentile=85.0, pb_percentile=30.0),
            enriched_industry=EnrichedIndustry(target_pe_premium=5.0),
        )

    def test_high_pe_scores_higher_in_reverse(self, config, ctx_low_pe, ctx_high_pe):
        """PE 反转：高分位（高PE=周期底部）得分应高于低分位"""
        scorer = CyclicalScorer(config, {})
        score_low, _, _ = scorer.score_valuation(ctx_low_pe)
        score_high, _, _ = scorer.score_valuation(ctx_high_pe)
        assert score_high > score_low, (
            f"周期反转下高PE分位应得分更高，实际 low={score_low}, high={score_high}"
        )

    def test_industry_note_mentions_reverse(self, config):
        scorer = CyclicalScorer(config, {})
        note = scorer._industry_note()
        assert "反转" in note

    def test_score_valuation_returns_valid_score(self, config, ctx_high_pe):
        scorer = CyclicalScorer(config, {})
        score, detail, risks = scorer.score_valuation(ctx_high_pe)
        assert 0 <= score <= 10
        assert isinstance(detail, str)
        assert isinstance(risks, list)

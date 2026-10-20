"""估值适用性回归：异常快照不能借残留充实数据获得加分。"""
from datetime import date

import pytest

from analysis.scorers.bank import BankScorer
from analysis.scorers.cyclical import CyclicalScorer
from analysis.scorers.general import GeneralScorer
from analysis.valuation import ValuationAnalyzer
from data.enrichers.industry_enricher import IndustryEnricher
from data.enrichers.valuation_enricher import ValuationEnricher
from data.schemas import (
    AnalysisContext,
    DimensionSufficiency,
    EnrichedIndustry,
    EnrichedValuation,
    IndustryData,
    PeerBasicInfo,
    SufficiencyLevel,
    ValuationData,
)

INVALID = [None, 0, -32.03, float("nan"), float("inf"), float("-inf")]
CONFIG = {"valuation": {
    "pe_percentile": {"tiers": [{"max": 100, "score": 4}]},
    "pb_percentile": {"tiers": [{"max": 100, "score": 2}]},
    "industry_premium": {"tiers": [{"max": 100, "score": 4}]},
}}


def context(pe: float | None = 20.0, pb: float | None = 2.0, ps: float | None = 3.0):
    ctx = AnalysisContext(
        symbol="688326", name="经纬恒润",
        valuation_data=ValuationData(symbol="688326", date=date(2026, 10, 20),
                                     pe_ttm=pe, pb=pb, ps_ttm=ps),
        enriched_valuation=EnrichedValuation(pe_percentile=10, pb_percentile=10),
        enriched_industry=EnrichedIndustry(target_pe_premium=-150.6, industry_median_pe=63.35),
    )
    ctx.sufficiency.valuation = DimensionSufficiency(
        level=SufficiencyLevel.SUFFICIENT, sample_count=120)
    return ctx


@pytest.mark.parametrize("value", INVALID)
@pytest.mark.parametrize("scorer_type", [GeneralScorer, BankScorer, CyclicalScorer])
def test_invalid_current_pe_cannot_score_stale_percentile_or_premium(value, scorer_type):
    ctx = context(pe=value, pb=None, ps=None)
    score, detail, risks = scorer_type(CONFIG, {}).score_valuation(ctx)
    assert score == 0
    assert "-151%" not in detail
    assert not risks


@pytest.mark.parametrize("value", INVALID)
@pytest.mark.parametrize("scorer_type", [GeneralScorer, BankScorer, CyclicalScorer])
def test_invalid_current_pb_cannot_score_stale_percentile(value, scorer_type):
    score, detail, _ = scorer_type(CONFIG, {}).score_valuation(context(pe=None, pb=value, ps=None))
    assert score == 0
    if value == 0:
        assert "PB 为负" not in detail


@pytest.mark.parametrize("value", INVALID)
def test_all_invalid_has_no_dimension_score_even_when_sufficiency_is_stale(value):
    result = ValuationAnalyzer().analyze(context(pe=value, pb=value, ps=value), CONFIG)
    assert result.status == "unavailable"
    assert result.score is None
    assert result.metrics.get("pe_percentile") is None
    assert result.metrics.get("pb_percentile") is None
    assert result.metrics.get("target_pe_premium") is None
    assert not result.metrics["valuation_applicability"]["pe_ttm"]["applicable"]


def test_loss_pe_preserved_but_valid_pb_keeps_its_own_score():
    result = ValuationAnalyzer().analyze(context(pe=-32.03, pb=2, ps=None), CONFIG)
    assert result.metrics["pe_ttm"] == -32.03
    assert result.metrics["pe_percentile"] is None
    assert result.metrics["pb_percentile"] == 10
    assert result.score == 2
    assert "不适用" in result.summary


@pytest.mark.parametrize("pct", [-1, 101, float("nan"), float("inf")])
@pytest.mark.parametrize("scorer_type", [GeneralScorer, BankScorer, CyclicalScorer])
def test_invalid_percentiles_do_not_score(pct, scorer_type):
    ctx = context()
    ctx.enriched_valuation = EnrichedValuation(pe_percentile=pct, pb_percentile=pct)
    ctx.enriched_industry = None
    assert scorer_type(CONFIG, {}).score_valuation(ctx)[0] == 0


@pytest.mark.parametrize("pe", INVALID)
def test_industry_does_not_compare_invalid_target_pe(pe):
    ctx = context(pe=pe)
    ctx.industry_data = IndustryData(
        symbol="688326", industry="软件开发", sector="科技", peers=["a", "b", "c"],
        top_peers=[PeerBasicInfo(symbol="a", pe_ttm=63.35, pb=2)],
    )
    IndustryEnricher().enrich(ctx)
    assert ctx.enriched_industry is not None
    assert ctx.enriched_industry.target_pe_premium is None


def test_positive_normal_score_and_valid_discount_are_unchanged():
    ctx = context()
    ctx.enriched_industry = EnrichedIndustry(target_pe_premium=-10, industry_median_pe=22)
    assert GeneralScorer(CONFIG, {}).score_valuation(ctx)[0] == 10


@pytest.mark.parametrize("invalid", INVALID)
def test_hk_ps_snapshot_is_independently_available(invalid):
    ctx = context(pe=invalid, pb=invalid, ps=2)
    ctx.market = "hk"
    ValuationEnricher().enrich(ctx)
    assert ctx.sufficiency.valuation.level == SufficiencyLevel.PARTIAL
    result = ValuationAnalyzer().analyze(ctx)
    assert result.status == "partial"
    assert result.metrics["ps_ttm"] == 2

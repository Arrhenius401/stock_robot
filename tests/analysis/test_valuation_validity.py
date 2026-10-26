"""估值适用性回归：异常快照不能借残留充实数据获得加分。"""
from datetime import date, timedelta

import pytest

from analysis.scorers.bank import BankScorer
from analysis.scorers.cyclical import CyclicalScorer
from analysis.scorers.general import GeneralScorer
from analysis.valuation import ValuationAnalyzer
from analysis.valuation_rules import valuation_evidence
from data.enrichers.industry_enricher import IndustryEnricher
from data.enrichers.valuation_enricher import ValuationEnricher
from data.schemas import (
    AnalysisContext,
    DailyValuationPoint,
    DimensionSufficiency,
    EnrichedIndustry,
    EnrichedValuation,
    IndustryData,
    PeerBasicInfo,
    SufficiencyLevel,
    ValuationData,
    ValuationHistorySummary,
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


def history_context(pe: float | None = 20.0, pb: float | None = 2.0, ps: float | None = 3.0):
    ctx = context(pe=pe, pb=pb, ps=ps)
    ctx.enriched_valuation = EnrichedValuation(
        daily_points=[
            DailyValuationPoint(trade_date=date(2026, 1, 1) + timedelta(days=index),
                                close=1.0, pe=pe, pb=pb)
            for index in range(120)
        ],
        pe_percentile=10, pb_percentile=10)
    return ctx


@pytest.mark.parametrize("ps", [None, 0, -1, float("nan"), float("inf")])
def test_ps_is_disclosure_only_and_does_not_block_ok_status(ps):
    result = ValuationAnalyzer().analyze(history_context(ps=ps), CONFIG)
    assert result.status == "ok"
    assert result.score == 10
    assert not result.metrics["valuation_applicability"]["ps_ttm"]["applicable"]


def test_loss_pe_with_valid_pb_remains_partial():
    result = ValuationAnalyzer().analyze(history_context(pe=-32.03, pb=2, ps=None), CONFIG)
    assert result.score == 10
    assert result.status == "partial"


def test_bank_without_pe_remains_partial():
    config = {**CONFIG, "meta": {"strategy_key": "BankScorer"}}
    result = ValuationAnalyzer().analyze(history_context(pe=None, pb=2, ps=None), config)
    assert result.score == 10
    assert result.status == "partial"


def test_ps_missing_does_not_override_insufficient_history():
    ctx = history_context(ps=None)
    if ctx.enriched_valuation is not None:
        ctx.enriched_valuation.daily_points = ctx.enriched_valuation.daily_points[:119]
    result = ValuationAnalyzer().analyze(ctx, CONFIG)
    assert result.status == "partial"


@pytest.mark.parametrize("value", INVALID)
@pytest.mark.parametrize("scorer_type", [GeneralScorer, BankScorer, CyclicalScorer])
def test_invalid_current_pe_cannot_score_stale_percentile_or_premium(value, scorer_type):
    ctx = context(pe=value, pb=None, ps=None)
    score, detail, risks = scorer_type(CONFIG, {}).score_valuation(ctx)
    assert score is None
    assert "-151%" not in detail
    assert not risks


@pytest.mark.parametrize("value", INVALID)
@pytest.mark.parametrize("scorer_type", [GeneralScorer, BankScorer, CyclicalScorer])
def test_invalid_current_pb_cannot_score_stale_percentile(value, scorer_type):
    score, detail, _ = scorer_type(CONFIG, {}).score_valuation(context(pe=None, pb=value, ps=None))
    assert score is None
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
    assert result.score == 10
    assert "不适用" in result.summary


@pytest.mark.parametrize("pct", [-1, 101, float("nan"), float("inf")])
@pytest.mark.parametrize("scorer_type", [GeneralScorer, BankScorer, CyclicalScorer])
def test_invalid_percentiles_do_not_score(pct, scorer_type):
    ctx = context()
    ctx.enriched_valuation = EnrichedValuation(pe_percentile=pct, pb_percentile=pct)
    ctx.enriched_industry = None
    assert scorer_type(CONFIG, {}).score_valuation(ctx)[0] is None


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


@pytest.mark.parametrize("metric", ["pe", "pb"])
@pytest.mark.parametrize("value", [-32.03, 0])
def test_hk_known_nonpositive_snapshot_blocks_its_old_positive_history(metric, value):
    ctx = context(pe=20, pb=2, ps=None)
    ctx.market = "hk"
    assert ctx.valuation_data is not None
    setattr(ctx.valuation_data, "pe_ttm" if metric == "pe" else "pb", value)
    ctx.enriched_valuation = EnrichedValuation(
        pe_percentile=10, pb_percentile=10,
        pe_history_summary=ValuationHistorySummary(
            source="百度", sample_count=130, reference=20.0, median=20.0, low=15.0, high=25.0,
            start=date(2026, 1, 1), as_of=date(2026, 10, 20)),
        pb_history_summary=ValuationHistorySummary(
            source="百度", sample_count=130, reference=2.0, median=2.0, low=1.5, high=2.5,
            start=date(2026, 1, 1), as_of=date(2026, 10, 20)),
    )
    pe, pb, _ = valuation_evidence(ctx)
    assert (pe, pb) == ((None, 10) if metric == "pe" else (10, None))
    analyzed = ValuationAnalyzer().analyze(ctx, CONFIG)
    assert analyzed.metrics[f"{metric}_percentile"] is None
    assert not analyzed.metrics["historical_valuation_applicability"][f"{metric}_percentile"]["applicable"]
    assert f"{metric.upper()} 分位 10.00%" not in analyzed.score_detail


@pytest.mark.parametrize("metric", ["pe", "pb"])
def test_hk_missing_snapshot_keeps_independent_history_percentile(metric):
    ctx = context()
    ctx.market = "hk"
    assert ctx.valuation_data is not None
    setattr(ctx.valuation_data, "pe_ttm" if metric == "pe" else "pb", None)
    ctx.enriched_valuation = EnrichedValuation(
        pe_percentile=10, pb_percentile=10,
        pe_history_summary=ValuationHistorySummary(source="百度", sample_count=130, reference=20.0),
        pb_history_summary=ValuationHistorySummary(source="百度", sample_count=130, reference=2.0),
    )
    assert valuation_evidence(ctx)[:2] == (10, 10)

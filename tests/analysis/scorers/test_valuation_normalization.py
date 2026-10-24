"""同行改进后的估值子项归一化回归。"""
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from analysis.config_loader import ConfigLoader
from analysis.scorers.bank import BankScorer
from analysis.scorers.cyclical import CyclicalScorer
from analysis.scorers.general import GeneralScorer
from analysis.valuation import ValuationAnalyzer
from data.schemas import (
    AnalysisContext,
    DimensionSufficiency,
    EnrichedIndustry,
    EnrichedValuation,
    SufficiencyLevel,
    ValuationData,
)

CONFIG_DIR = Path(__file__).resolve().parents[3] / "src/analysis/config"


def _ctx(pe: float | None = 20, pb: float | None = 2,
         pe_pct: float | None = 30, pb_pct: float | None = 30):
    ctx = AnalysisContext(symbol="000333", name="样例",
        valuation_data=ValuationData(symbol="000333", date=date(2026, 10, 23), pe_ttm=pe, pb=pb),
        enriched_valuation=EnrichedValuation(pe_percentile=pe_pct, pb_percentile=pb_pct))
    ctx.sufficiency.valuation = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT)
    return ctx


@pytest.mark.parametrize(("scorer_type", "industry", "pe_pct", "pb_pct", "expected"), [
    (GeneralScorer, "家用电器", 50, 50, 6.7),
    (BankScorer, "银行", 30, 30, 6.7),
    (CyclicalScorer, "煤炭", 85, 50, 8.3),
])
def test_missing_peer_premium_redistributes_only_available_items(scorer_type, industry, pe_pct, pb_pct, expected):
    config = ConfigLoader(CONFIG_DIR).load(industry)
    score, detail, _ = scorer_type(config, {}).score_valuation(_ctx(pe_pct=pe_pct, pb_pct=pb_pct))
    assert score == expected
    assert "折算" in detail
    assert "不计分" in detail


@pytest.mark.parametrize(("scorer_type", "industry"), [(GeneralScorer, "家用电器"), (BankScorer, "银行"), (CyclicalScorer, "煤炭")])
def test_only_pb_full_score_scales_to_ten(scorer_type, industry):
    config = ConfigLoader(CONFIG_DIR).load(industry)
    assert scorer_type(config, {}).score_valuation(_ctx(pe=None, pb_pct=10))[0] == 10


def test_valid_zero_score_remains_in_denominator():
    config = ConfigLoader(CONFIG_DIR).load("家用电器")
    score, _, _ = GeneralScorer(config, {}).score_valuation(_ctx(pe_pct=99, pb_pct=10))
    assert score == 3.3


def test_disabled_subitem_is_not_in_denominator():
    config = ConfigLoader(CONFIG_DIR).load("家用电器")
    config["valuation"]["pe_percentile"]["enabled"] = False
    assert GeneralScorer(config, {}).score_valuation(_ctx(pb_pct=10))[0] == 10


@pytest.mark.parametrize("scorer_type", [GeneralScorer, BankScorer, CyclicalScorer])
def test_all_disabled_or_missing_has_no_score(scorer_type):
    config = ConfigLoader(CONFIG_DIR).load("家用电器")
    assert scorer_type(config, {}).score_valuation(_ctx(pe_pct=None, pb_pct=None))[0] is None
    for item in ("pe_percentile", "pb_percentile", "industry_premium"):
        config["valuation"][item]["enabled"] = False
    assert scorer_type(config, {}).score_valuation(_ctx())[0] is None
    config["valuation"]["enabled"] = False
    assert scorer_type(config, {}).score_valuation(_ctx())[0] is None


def test_analyzer_preserves_none_when_snapshots_have_no_scoring_evidence():
    config = ConfigLoader(CONFIG_DIR).load("家用电器")
    result = ValuationAnalyzer().analyze(_ctx(pe_pct=None, pb_pct=None), config)
    assert result.score is None
    assert "不计分" in result.score_detail


def test_comparison_premium_uses_same_batch_target_and_never_legacy_fallback():
    from analysis.valuation_rules import valuation_evidence

    ctx = _ctx(pe=200)
    ctx.enriched_industry = EnrichedIndustry(industry_median_pe=100, target_pe_premium=100,
        comparison={"valuation": {"metrics": [{"key": "pe_ttm", "target": 20, "median": 25, "sample_count": 5, "status": "ok"}]}})
    assert valuation_evidence(ctx)[2] == pytest.approx(-20)
    comparison = deepcopy(ctx.enriched_industry.comparison)
    comparison["valuation"]["metrics"][0]["sample_count"] = 4
    ctx.enriched_industry.comparison = comparison
    assert valuation_evidence(ctx)[2] is None


def test_hk_does_not_gain_valuation_score_from_shared_config():
    ctx = _ctx()
    ctx.market = "hk"
    config = ConfigLoader(CONFIG_DIR).load("家用电器")
    assert GeneralScorer(config, {}).score_valuation(ctx)[0] is None


def test_removed_industry_bonuses_and_legacy_margin_risk_stay_removed():
    ctx = _ctx()
    ctx.enriched_industry = EnrichedIndustry(peer_count=50, target_market_cap_rank=1,
        industry_median_gross_margin=0.9)
    config = ConfigLoader(CONFIG_DIR).load("家用电器")
    score, detail, risks = GeneralScorer(config, {}).score_industry(ctx)
    assert score is None
    assert risks == []
    assert "基础分" not in detail

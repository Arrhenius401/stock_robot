"""US 行业来源接入与分析前充实边界。"""
import pytest

from analysis.industry import IndustryAnalyzer
from core.pipeline import Pipeline
from core.registry import Registry
from data.collection_state import ValueResult, make_record
from data.schemas import AnalysisContext, IndustryData
from utils.config import Config


@pytest.mark.parametrize("market", ["a-shares", "hk", "us"])
@pytest.mark.parametrize("kind", ["industry", "valuation", "news"])
def test_empty_single_value_is_missing_in_every_market(market, kind):
    ctx = AnalysisContext(symbol="TEST", name="目标", market=market)
    Pipeline._assign_to_context(ctx, kind, [])
    assert getattr(ctx, f"{kind}_data") is None


@pytest.mark.parametrize("symbol", ["ANT", "SPCX"])
def test_missing_us_industry_does_not_crash_enrichment(mocker, tmp_path, symbol):
    # 用户现场报错：'list' object has no attribute 'comparison_snapshot'。
    from data.enrichers.us_industry_enricher import UsIndustryEnricher

    record = make_record("东财F10", "failed", error_kind="fetch_failed", error_message="行业数据缺失")
    mocker.patch("data.us_industry.us_industry_result", return_value=ValueResult(None, record))
    pipeline = Pipeline(Registry(), Config(tmp_path), llm_enabled=False)
    ctx = pipeline.collect(symbol, symbol, market="us", data_types=["industry"])

    assert ctx.industry_data is None
    assert ctx.collection_records["industry"].result_state == "failed"
    UsIndustryEnricher().enrich(ctx)
    assert ctx.enriched_industry is None
    assert ctx.sufficiency.industry.level == "insufficient"


def test_collect_us_industry_preserves_diagnostic_and_cache(mocker, tmp_path):
    data = IndustryData(symbol="AAPL", industry="系统软件", sector="")
    record = make_record("东财F10", "partial", error_kind="unverified_basis", error_message="行情未核实")
    fetch = mocker.patch("data.us_industry.us_industry_result", return_value=ValueResult(data, record))
    pipeline = Pipeline(Registry(), Config(tmp_path), llm_enabled=False)
    ctx = pipeline.collect("AAPL", "苹果", market="us", data_types=["industry"], refresh_cache=True)
    assert ctx.industry_data is data
    assert ctx.collection_records["industry"].result_state == "partial"
    assert fetch.call_args.kwargs == {"cache": pipeline._cache, "refresh": True}


def test_us_enrichment_precedes_industry_analysis_and_never_calls_a_service(mocker, tmp_path):
    registry = Registry()
    registry.register_analysis_module(IndustryAnalyzer())
    config = Config(tmp_path)
    config.data["analysis"]["extra_enabled"] = True
    pipeline = Pipeline(registry, config, llm_enabled=False)
    ctx = AnalysisContext(symbol="AAPL", name="苹果", market="us",
        industry_data=IndustryData(symbol="AAPL", industry="系统软件", sector=""))
    mocker.patch.object(pipeline, "collect", return_value=ctx)
    a_service = mocker.patch("data.peer_comparison.PeerComparisonService.enrich")
    values, _, output = pipeline.run("AAPL", "苹果", market="us", dimension="industry")
    a_service.assert_not_called()
    assert output.enriched_industry is not None
    assert values[0].score is None
    assert values[0].metrics["comparison"]["version"] == 1
    assert "不是 GICS/申万" in values[0].industry_note


def test_us_financial_path_builds_ttm_scoring_facts(mocker, tmp_path):
    config = Config(tmp_path)
    config.data['analysis']['extra_enabled'] = True
    pipeline = Pipeline(Registry(), config, llm_enabled=False)
    ctx = AnalysisContext(symbol='AAPL', name='苹果', market='us')
    mocker.patch.object(pipeline, 'collect', return_value=ctx)
    facts = mocker.patch('data.financial_scoring_facts.FinancialScoringFactsService.enrich', return_value=ctx)
    pipeline.run('AAPL', '苹果', market='us', dimension='financial')
    facts.assert_called_once()


def test_old_us_financial_cache_cannot_skip_new_evidence(mocker, tmp_path):
    import json
    from datetime import date

    from data.schemas import FinancialData

    pipeline = Pipeline(Registry(), Config(tmp_path), llm_enabled=False)
    row = FinancialData(symbol='AAPL', fiscal_quarter=date(2026, 9, 30), revenue=100, net_profit=20,
                        precision_version=1)
    pipeline._cache.put('financial', 'AAPL', 'latest', json.dumps([row.model_dump(mode='json')]))
    assert pipeline._get_cached('AAPL', 'financial') is None
    fresh = row.model_dump(mode='json') | {'_us_financial_version': 1}
    pipeline._cache.put('financial', 'AAPL', 'latest', json.dumps([fresh]))
    mocker.patch.object(pipeline, '_is_healthy', return_value=True)
    assert pipeline._get_cached('AAPL', 'financial') is not None

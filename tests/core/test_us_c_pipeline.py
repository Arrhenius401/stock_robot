"""US 行业来源接入与分析前充实边界。"""
from analysis.industry import IndustryAnalyzer
from core.pipeline import Pipeline
from core.registry import Registry
from data.collection_state import ValueResult, make_record
from data.schemas import AnalysisContext, IndustryData
from utils.config import Config


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

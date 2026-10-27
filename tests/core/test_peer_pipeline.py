"""比较服务必须在分析前同步完成，失败不得阻断整份报告。"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

from analysis.base import AnalysisModule
from core.pipeline import Pipeline, _cacheable
from core.registry import Registry
from data.base import DataSource
from data.schemas import AnalysisContext, AnalysisResult, IndustryData
from tests.support import FetchResultMixin
from utils.config import Config


def test_comparison_finishes_before_analysis(monkeypatch, tmp_path):
    events = []

    class Service:
        def __init__(self, cache):
            self.cache = cache

        def enrich(self, ctx, *, refresh=False):
            events.append(("comparison", refresh))
            ctx.industry_data.peer_industry = "白酒Ⅲ"
            return ctx

    monkeypatch.setitem(sys.modules, "data.peer_comparison", SimpleNamespace(PeerComparisonService=Service))
    ctx = AnalysisContext(symbol="600519", name="测试", industry_data=IndustryData(
        symbol="600519", industry="白酒Ⅲ", sector="食品饮料"))
    registry = Registry()
    analyzer = MagicMock(spec=AnalysisModule)
    analyzer.dimension = "industry"

    def analyze(context, _config):
        events.append(("analysis", context.industry_data.peer_industry))
        return AnalysisResult(dimension="industry", status="partial", summary="同行样本不足")

    analyzer.analyze.side_effect = analyze
    registry.register_analysis_module(analyzer)
    pipeline = Pipeline(registry, Config(tmp_path / "state"), llm_enabled=False)
    monkeypatch.setattr(pipeline, "collect", lambda *args, **kwargs: ctx)
    results, _, _ = pipeline.run("600519", "测试", refresh_cache=True)
    assert events == [("comparison", True), ("analysis", "白酒Ⅲ")]
    assert len(results) == 1


def test_comparison_error_isolated_and_visible(monkeypatch, tmp_path, caplog):
    class Service:
        def __init__(self, cache):
            pass

        def enrich(self, ctx, *, refresh=False):
            raise RuntimeError("采集服务异常")

    monkeypatch.setitem(sys.modules, "data.peer_comparison", SimpleNamespace(PeerComparisonService=Service))
    ctx = AnalysisContext(symbol="600519", name="测试", industry_data=IndustryData(
        symbol="600519", industry="白酒Ⅲ", sector="食品饮料"))
    pipeline = Pipeline(Registry(), Config(tmp_path / "state"), llm_enabled=False)
    monkeypatch.setattr(pipeline, "collect", lambda *args, **kwargs: ctx)
    _, _, returned = pipeline.run("600519", "测试")
    assert returned.enriched_industry is None
    assert "获取失败" in returned.sufficiency.industry.reason
    assert "同行比较失败" in caplog.text


def test_old_industry_cache_not_reused_for_new_comparison():
    assert not _cacheable("a-shares", "industry")
    assert not _cacheable("hk", "industry")
    assert _cacheable("a-shares", "financial")


def test_industry_breaker_keeps_dedicated_cache_readable(monkeypatch, tmp_path):
    """断路器打开时适配器只读专用快照，禁止绕过断路器重发请求。"""
    monkeypatch.setattr("core.pipeline.time.sleep", lambda _: None)
    calls = []

    class CachedIndustrySource(FetchResultMixin, DataSource):
        def supports(self, market, data_type):
            return data_type == "industry"

        def fetch(self, symbol, **kwargs):
            calls.append(kwargs)
            assert kwargs["comparison_cache_only"] is True
            assert kwargs["comparison_cache"] is pipeline._cache
            return [IndustryData(symbol=symbol, industry="白酒Ⅲ", sector="食品饮料",
                                 comparison_ready=True)]

    registry = Registry()
    registry.register_data_source(CachedIndustrySource())
    pipeline = Pipeline(registry, Config(tmp_path / "state"), llm_enabled=False)
    for _ in range(3):
        pipeline._breaker.record_failure("600519", "industry")
    assert pipeline._breaker.is_open("600519", "industry")
    ctx = pipeline.collect("600519", "测试", refresh_cache=True, data_types=["industry"])
    assert ctx.industry_data is not None
    assert ctx.industry_data.industry == "白酒Ⅲ"
    assert ctx.collection_records["industry"].result_state == "complete"
    assert len(calls) == 1
    assert pipeline._breaker.is_open("600519", "industry")


def test_partial_industry_pool_does_not_claim_network_recovery(monkeypatch, tmp_path):
    """分类可用而报价失败时保留经营入口，同时累计真实上游失败。"""
    monkeypatch.setattr("core.pipeline.time.sleep", lambda _: None)

    class PartialIndustrySource(FetchResultMixin, DataSource):
        def supports(self, market, data_type):
            return data_type == "industry"

        def fetch(self, symbol, **kwargs):
            return [IndustryData(symbol=symbol, industry="软件服务", sector="资讯科技",
                                 comparison_ready=True,
                                 comparison_snapshot={"errors": ["估值请求失败"]})]

    registry = Registry()
    registry.register_data_source(PartialIndustrySource())
    pipeline = Pipeline(registry, Config(tmp_path / "state"), llm_enabled=False)
    for _ in range(3):
        ctx = pipeline.collect("00700.HK", "测试", market="hk", data_types=["industry"])
        assert ctx.industry_data is not None
        assert ctx.collection_records["industry"].result_state == "partial"
    assert pipeline._breaker.is_open("00700.HK", "industry")


def test_unknown_industry_keeps_diagnostic_data_with_failed_state(tmp_path, monkeypatch):
    class UnknownIndustrySource(FetchResultMixin, DataSource):
        def supports(self, market, data_type):
            return data_type == "industry"

        def fetch(self, symbol, **kwargs):
            return [IndustryData(symbol=symbol, industry="未知", sector="")]

    monkeypatch.setattr("core.pipeline.time.sleep", lambda _: None)
    registry = Registry()
    registry.register_data_source(UnknownIndustrySource())
    pipeline = Pipeline(registry, Config(tmp_path / "state"), llm_enabled=False)
    ctx = pipeline.collect("00700.HK", "测试", market="hk", data_types=["industry"])
    assert ctx.industry_data is not None and ctx.industry_data.industry == "未知"
    assert ctx.collection_records["industry"].result_state == "failed"

from analysis.base import AnalysisModule
from typing import Any, cast

from core.registry import Registry
from data.base import DataSource
from data.schemas import AnalysisResult
from llm.base import LLMBackend


class FakeSource(DataSource):
    def supports(self, market, data_type):
        return market == "a-shares" and data_type in ("price", "financial")
    def fetch(self, symbol, **kwargs):
        return []


class AnotherSource(DataSource):
    def supports(self, market, data_type):
        return market == "a-shares" and data_type == "valuation"
    def fetch(self, symbol, **kwargs):
        return []


class TestRegistry:
    def test_register_and_get_data_sources(self):
        r = Registry()
        r.register_data_source(FakeSource())
        sources = r.get_data_sources("a-shares", "price")
        assert len(sources) == 1
        assert isinstance(sources[0], FakeSource)

    def test_no_match_returns_empty_list(self):
        r = Registry()
        r.register_data_source(FakeSource())
        sources = r.get_data_sources("us", "price")
        assert sources == []

    def test_multiple_sources_ordered(self):
        r = Registry()
        r.register_data_source(FakeSource())
        r.register_data_source(AnotherSource())
        price_sources = r.get_data_sources("a-shares", "price")
        val_sources = r.get_data_sources("a-shares", "valuation")
        assert len(price_sources) == 1
        assert len(val_sources) == 1

    def test_register_analysis_module(self):
        r = Registry()

        class FakeMod(AnalysisModule):
            @property
            def dimension(self):
                return "fake"
            def analyze(self, ctx):
                return AnalysisResult(dimension=cast(Any, "fake"), status="ok", summary="ok", metrics={})

        r.register_analysis_module(FakeMod())
        results = r.get_analysis_modules()
        assert len(results) == 1
        assert results[0].dimension == "fake"

    def test_register_llm_backend(self):
        class FakeLLM(LLMBackend):
            @property
            def model_name(self):
                return "fake"
            def generate(self, prompt, **kwargs):
                return "response"

        r = Registry()
        r.register_llm_backend(FakeLLM())
        llm = r.get_llm_backend("fake")
        assert llm is not None
        assert llm.model_name == "fake"

    def test_get_nonexistent_llm_returns_none(self):
        r = Registry()
        assert r.get_llm_backend("nonexistent") is None

    def test_register_llm_backend_by_provider_name(self):
        class GPTAdapter(LLMBackend):
            @property
            def model_name(self):
                return "gpt-4o"
            def generate(self, prompt, **kwargs):
                return "gpt-response"

        r = Registry()
        r.register_llm_backend(GPTAdapter(), provider="openai")
        llm = r.get_llm_backend("openai")
        assert llm is not None

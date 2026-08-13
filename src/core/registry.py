"""注册机制 — 管理数据源、分析模块、LLM 后端的注册与查找"""
from analysis.base import AnalysisModule
from data.base import DataSource
from llm.base import LLMBackend


class Registry:
    def __init__(self):
        self._data_sources: list[DataSource] = []
        self._analysis_modules: list[AnalysisModule] = []
        self._llm_backends: dict[str, LLMBackend] = {}

    def register_data_source(self, source: DataSource):
        self._data_sources.append(source)

    def get_data_sources(self, market: str, data_type: str) -> list[DataSource]:
        return [s for s in self._data_sources if s.supports(market, data_type)]

    def register_analysis_module(self, module: AnalysisModule):
        self._analysis_modules.append(module)

    def get_analysis_modules(self) -> list[AnalysisModule]:
        return list(self._analysis_modules)

    def register_llm_backend(self, backend: LLMBackend, provider: str | None = None):
        key = provider or backend.model_name
        self._llm_backends[key] = backend

    def get_llm_backend(self, provider: str) -> LLMBackend | None:
        return self._llm_backends.get(provider)

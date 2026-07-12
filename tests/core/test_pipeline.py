from unittest.mock import MagicMock
from datetime import date
from core.pipeline import Pipeline
from core.registry import Registry
from data.schemas import (
    AnalysisContext, AnalysisResult, FinancialData, PriceData,
    ValuationData, IndustryData, NewsData,
)
from data.base import DataSource
from llm.base import LLMBackend


def make_test_registry():
    """构建测试用注册表"""
    class MockDataSource(DataSource):
        def supports(self, market, data_type):
            return True

        def fetch(self, symbol, **kwargs):
            data_type = kwargs.get("data_type", "price")
            if data_type == "price":
                return [PriceData(symbol=symbol, trade_date=date(2026,7,1), open=10, high=11, low=9.5, close=10.5, volume=1e6)]
            elif data_type == "financial":
                return [FinancialData(symbol=symbol, fiscal_quarter=date(2025,12,31), revenue=45e9, net_profit=8.5e9, total_assets=500e9, total_equity=45e9, operating_cash_flow=12e9)]
            elif data_type == "valuation":
                return [ValuationData(symbol=symbol, date=date(2026,7,1), pe_ttm=7.5, pb=0.85, ps_ttm=1.2)]
            elif data_type == "industry":
                return [IndustryData(symbol=symbol, industry="银行", sector="金融", peers=["600036"])]
            elif data_type == "news":
                return [NewsData(symbol=symbol, date=date(2026,7,1), headlines=["利好公告"])]
            return []

    reg = Registry()
    reg.register_data_source(MockDataSource())

    from analysis.base import AnalysisModule
    for dim in ["financial", "technical", "valuation", "industry", "sentiment"]:
        mod = MagicMock(spec=AnalysisModule)
        mod.dimension = dim
        mod.analyze.return_value = AnalysisResult(
            dimension=dim, status="ok", summary=f"{dim} analysis", metrics={}
        )
        reg.register_analysis_module(mod)
    return reg


class TestPipeline:
    def test_collect_data_populates_context(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        ctx = pipeline.collect("000001", "平安银行", "a-shares")
        assert ctx.symbol == "000001"
        assert ctx.price_data is not None
        assert ctx.financial_data is not None

    def test_run_without_llm(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        results, commentary = pipeline.run("000001", "平安银行")
        assert len(results) == 5
        assert all(isinstance(r, AnalysisResult) for r in results)

    def test_collect_refresh_cache_ignores_cache(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        ctx = pipeline.collect("000001", "平安银行", "a-shares", refresh_cache=True)
        assert ctx.price_data is not None

    def test_single_dimension_filter(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        results, _ = pipeline.run("000001", "平安银行", dimension="financial")
        assert len(results) == 1
        assert results[0].dimension == "financial"


class TestPipelineProgress:
    def test_collect_calls_on_progress_for_each_data_type(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        calls = []

        def on_progress(stage, current, total, label):
            calls.append((stage, current, total, label))

        pipeline.collect("000001", "平安银行", "a-shares", on_progress=on_progress)

        assert len(calls) == 5
        expected_labels = {"采集财务数据", "采集价格数据", "采集估值数据", "采集行业数据", "采集舆情数据"}
        actual_labels = {c[3] for c in calls}
        assert actual_labels == expected_labels
        assert all(c[0] == "collect" for c in calls)
        assert {c[1] for c in calls} == set(range(1, 6))
        assert all(c[2] == 5 for c in calls)

    def test_run_calls_on_progress_for_collect_and_analyze(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        calls = []

        def on_progress(stage, current, total, label):
            calls.append((stage, current, total, label))

        pipeline.run("000001", "平安银行", on_progress=on_progress)

        collect_calls = [c for c in calls if c[0] == "collect"]
        analyze_calls = [c for c in calls if c[0] == "analyze"]
        assert len(collect_calls) == 5
        assert len(analyze_calls) == 5

    def test_on_progress_none_does_not_break(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)

        ctx = pipeline.collect("000001", "平安银行", on_progress=None)
        assert ctx.price_data is not None

        results, _ = pipeline.run("000001", "平安银行", on_progress=None)
        assert len(results) == 5

    def test_run_single_dimension_reports_correct_totals(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        calls = []

        def on_progress(stage, current, total, label):
            calls.append((stage, current, total, label))

        pipeline.run("000001", "平安银行", dimension="financial", on_progress=on_progress)

        collect_calls = [c for c in calls if c[0] == "collect"]
        analyze_calls = [c for c in calls if c[0] == "analyze"]
        assert len(collect_calls) == 1
        assert collect_calls[0][1:3] == (1, 1)
        assert len(analyze_calls) == 1
        assert analyze_calls[0][1:3] == (1, 1)


class CountingLLM(LLMBackend):
    def __init__(self):
        self.calls = []

    @property
    def model_name(self):
        return "fake"

    def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        return "MOCK解读"


def _make_hermetic_pipeline(reg, llm, provider="openai"):
    """创建隔离的 Pipeline，避免受本地 ~/.stock_robot/config.yaml 影响"""
    from utils.config import Config
    reg.register_llm_backend(llm, provider=provider)
    # 强制使用 openai provider，覆盖本地配置
    cfg = Config()
    cfg.data["llm"]["provider"] = provider
    return Pipeline(registry=reg, config=cfg)


class TestGenerateCommentaryGuards:
    def test_skips_unavailable_dimensions(self):
        reg = Registry()
        llm = CountingLLM()
        pipeline = _make_hermetic_pipeline(reg, llm, "openai")
        results = [
            AnalysisResult(dimension="valuation", status="partial", summary="",
                           metrics={"pe_ttm": 7.5, "pb": 0.85, "ps_ttm": 1.2}),
            AnalysisResult(dimension="financial", status="unavailable",
                           summary="财务数据不可用", metrics={}),
        ]
        commentary = pipeline._generate_commentary("600350", "山东高速", results)
        assert commentary["financial"] == ""            # 跳过 LLM
        assert commentary["valuation"] == "MOCK解读"
        assert len(llm.calls) == 2                        # 1 维度 + 1 总结

    def test_skips_summary_when_all_unavailable(self):
        reg = Registry()
        llm = CountingLLM()
        pipeline = _make_hermetic_pipeline(reg, llm, "openai")
        results = [
            AnalysisResult(dimension="financial", status="unavailable", summary="x", metrics={}),
            AnalysisResult(dimension="technical", status="unavailable", summary="x", metrics={}),
        ]
        commentary = pipeline._generate_commentary("600350", "山东高速", results)
        assert commentary.get("summary", "") == ""
        assert len(llm.calls) == 0                        # 完全不调用 LLM

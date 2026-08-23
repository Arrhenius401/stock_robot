from datetime import date
from unittest.mock import MagicMock

from core.pipeline import Pipeline
from core.registry import Registry
from data.base import DataSource
from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    FinancialData,
    IndustryData,
    NewsData,
    PriceData,
    ValuationData,
)
from llm.base import LLMBackend


def make_test_registry():
    """构建测试用注册表"""
    class MockDataSource(DataSource):
        def supports(self, market, data_type):
            return True

        def fetch(self, symbol, **kwargs):
            data_type = kwargs.get("data_type", "price")
            if data_type == "price":
                return [PriceData(symbol=symbol, trade_date=date(2026,7,1), open=10, high=11, low=9.5, close=10.5, volume=1_000_000)]
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

    from analysis.base import AnalysisModule, DimensionName
    dims: list[DimensionName] = ["financial", "technical", "valuation", "industry", "sentiment"]
    for dim in dims:
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
        results, _, ctx = pipeline.run("000001", "平安银行")
        assert len(results) == 5
        assert all(isinstance(r, AnalysisResult) for r in results)
        assert isinstance(ctx, AnalysisContext)

    def test_collect_refresh_cache_ignores_cache(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        ctx = pipeline.collect("000001", "平安银行", "a-shares", refresh_cache=True)
        assert ctx.price_data is not None

    def test_single_dimension_filter(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        results, _, _ = pipeline.run("000001", "平安银行", dimension="financial")
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

        results, _, _ = pipeline.run("000001", "平安银行", on_progress=None)
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


class TestGenerateCommentary:
    def test_batch_commentary_with_mixed_results(self):
        reg = Registry()
        llm = CountingLLM()
        pipeline = _make_hermetic_pipeline(reg, llm, "openai")
        results = [
            AnalysisResult(dimension="valuation", status="ok", summary="",
                           metrics={"pe_ttm": 7.5, "pb": 0.85},
                           score=6.0, score_detail="PE偏低，PB合理"),
            AnalysisResult(dimension="financial", status="unavailable",
                           summary="财务数据不可用", metrics={}),
            AnalysisResult(dimension="technical", status="ok", summary="",
                           metrics={"close": 10.5},
                           score=7.0, score_detail="均线多头排列"),
        ]
        commentary = pipeline._generate_commentary("600350", "山东高速", results)
        assert "bulk" in commentary
        assert isinstance(commentary["bulk"], str)
        assert len(llm.calls) == 1  # 单次批量调用，不再逐维度+总结

    def test_batch_commentary_all_unavailable_still_does_batch(self):
        reg = Registry()
        llm = CountingLLM()
        pipeline = _make_hermetic_pipeline(reg, llm, "openai")
        results = [
            AnalysisResult(dimension="financial", status="unavailable", summary="x", metrics={}),
            AnalysisResult(dimension="technical", status="unavailable", summary="x", metrics={}),
        ]
        commentary = pipeline._generate_commentary("600350", "山东高速", results)
        assert "bulk" in commentary  # 批量调用仍然会执行
        assert len(llm.calls) == 1


class TestCacheHealth:
    """缓存健康校验：退化结果不写持久缓存"""

    def _make_pipeline(self, tmp_path, mocker):
        """构建隔离缓存管道的 Pipeline（缓存 DB 落在 tmp_path）"""
        from core.pipeline import Pipeline
        from core.registry import Registry
        from utils.config import Config

        reg = Registry()
        cfg = Config(config_dir=tmp_path)
        return Pipeline(registry=reg, config=cfg, llm_enabled=False)

    def test_industry_unknown_not_cached(self, tmp_path, mocker):
        """行业未知的结果不写持久缓存"""
        from data.schemas import IndustryData
        pipe = self._make_pipeline(tmp_path, mocker)
        ind = IndustryData(symbol="000001", industry="未知", sector="", peers=[], top_peers=[])
        pipe._set_cache("000001", "industry", [ind])
        assert pipe._get_cached("000001", "industry") is None

    def test_healthy_data_is_cached(self, tmp_path, mocker):
        """健康数据正常缓存"""
        from data.schemas import IndustryData
        pipe = self._make_pipeline(tmp_path, mocker)
        ind = IndustryData(symbol="000001", industry="银行", sector="金融", peers=["600000"], top_peers=[])
        pipe._set_cache("000001", "industry", [ind])
        cached = pipe._get_cached("000001", "industry")
        assert cached is not None and cached[0].industry == "银行"

    def test_financial_all_equity_missing_not_cached(self, tmp_path, mocker):
        """财务 equity 全缺失不写缓存"""
        from data.schemas import FinancialData
        pipe = self._make_pipeline(tmp_path, mocker)
        fin = FinancialData(symbol="000001", fiscal_quarter=date(2026, 6, 30))
        pipe._set_cache("000001", "financial", [fin])
        assert pipe._get_cached("000001", "financial") is None


class TestPipelineIndustryIntegration:
    """验证管道已正确集成行业分类和配置驱动打分"""

    def test_context_has_industry_after_collect(self):
        """collect 后 ctx 应有 sw_industry 和 style_category"""
        from core.pipeline import Pipeline
        from core.registry import Registry
        from data.akshare import AkShareAdapter

        reg = Registry()
        reg.register_data_source(AkShareAdapter())
        pipeline = Pipeline(registry=reg, llm_enabled=False)

        ctx = pipeline.collect("000001", "平安银行", refresh_cache=True)
        assert ctx.sw_industry != ""
        assert ctx.style_category != ""

    def test_analysis_results_have_scores(self):
        """分析结果应有配置驱动的分数"""
        from analysis.financial import FinancialAnalyzer
        from analysis.industry import IndustryAnalyzer
        from analysis.sentiment import SentimentAnalyzer
        from analysis.technical import TechnicalAnalyzer
        from analysis.valuation import ValuationAnalyzer
        from core.pipeline import Pipeline
        from core.registry import Registry
        from data.akshare import AkShareAdapter

        reg = Registry()
        reg.register_data_source(AkShareAdapter())
        reg.register_analysis_module(FinancialAnalyzer())
        reg.register_analysis_module(ValuationAnalyzer())
        reg.register_analysis_module(IndustryAnalyzer())
        reg.register_analysis_module(TechnicalAnalyzer())
        reg.register_analysis_module(SentimentAnalyzer())

        pipeline = Pipeline(registry=reg, llm_enabled=False)
        results, _, ctx = pipeline.run("000001", "平安银行")

        assert len(results) == 5
        assert ctx.sw_industry != ""
        # 至少有一个维度有分数
        scored = [r for r in results if r.score is not None]
        assert len(scored) > 0

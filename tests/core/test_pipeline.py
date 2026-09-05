from datetime import date
from unittest.mock import MagicMock

from core.pipeline import Pipeline
from core.registry import Registry
from data.base import DataSource
from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    DataSufficiency,
    FinancialData,
    IndustryData,
    NewsData,
    PriceData,
    RawSentimentData,
    RawSentimentItem,
    ValuationData,
)
from llm.base import LLMBackend
from utils.config import Config


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

    def test_legacy_financial_cache_is_bypassed_after_metric_fix(self, tmp_path, mocker):
        """旧口径财务缓存不应参与修正后的指标计算。"""
        pipe = self._make_pipeline(tmp_path, mocker)
        pipe._cache.put(
            "financial", "000001", "latest",
            '[{"symbol":"000001","fiscal_quarter":"2026-06-30",'
            '"total_assets":100,"total_equity":50,"roe":-7.48}]',
        )

        assert pipe._get_cached("000001", "financial") is None

    def test_empty_news_not_cached_as_healthy(self, tmp_path, mocker):
        """空舆情结果视为退化数据，避免覆盖可用旧缓存"""
        pipe = self._make_pipeline(tmp_path, mocker)
        news = NewsData(symbol="000001", date=date(2026, 8, 30), headlines=[])
        news._raw_sentiment = RawSentimentData(
            symbol="000001", fetch_date=date(2026, 8, 30), items=[],
        )

        pipe._set_cache("000001", "news", [news])

        assert pipe._get_cached("000001", "news") is None

    def test_legacy_empty_news_cache_is_ignored(self, tmp_path, mocker):
        """历史版本写入的空舆情缓存读取时应视为未命中"""
        pipe = self._make_pipeline(tmp_path, mocker)
        pipe._cache.put(
            "news", "000001", "latest",
            '[{"symbol": "000001", "date": "2026-08-30", "headlines": []}]',
        )

        assert pipe._get_cached("000001", "news") is None

    def test_news_uses_stale_cache_when_live_result_degraded(self, tmp_path, mocker):
        """实时舆情为空时，使用过期旧缓存兜底并标注来源"""
        class EmptyNewsSource(DataSource):
            def supports(self, market, data_type):
                return data_type == "news"

            def fetch(self, symbol, **kwargs):
                news = NewsData(symbol=symbol, date=date(2026, 8, 30), headlines=[])
                news._raw_sentiment = RawSentimentData(
                    symbol=symbol, fetch_date=date(2026, 8, 30), items=[],
                )
                return [news]

        reg = Registry()
        reg.register_data_source(EmptyNewsSource())
        pipe = Pipeline(registry=reg, config=Config(config_dir=tmp_path), llm_enabled=False)

        cached = NewsData(
            symbol="000001", date=date(2026, 8, 1),
            headlines=["旧新闻1", "旧新闻2", "旧公告"],
        )
        cached._raw_sentiment = RawSentimentData(
            symbol="000001",
            fetch_date=date(2026, 8, 1),
            items=[
                RawSentimentItem(
                    title="旧新闻1", source="news",
                    publish_date=date(2026, 8, 1),
                ),
                RawSentimentItem(
                    title="旧新闻2", source="news",
                    publish_date=date(2026, 8, 1),
                ),
                RawSentimentItem(
                    title="旧公告", source="announcement",
                    publish_date=date(2026, 8, 1),
                ),
            ],
        )
        pipe._set_cache("000001", "news", [cached])
        stale_value = pipe._cache.get_stale("news", "000001", "latest") or "[]"
        pipe._cache.put(
            "news", "000001", "latest", stale_value, ttl_seconds=0,
        )

        ctx = pipe.collect("000001", "平安银行", data_types=["news"])
        assert ctx.raw_sentiment is not None
        assert len(ctx.raw_sentiment.items) == 3
        assert ctx.raw_sentiment._from_stale_cache is True

        from data.enrichers.sentiment_enricher import SentimentEnricher

        enriched = SentimentEnricher().enrich(AnalysisContext(
            symbol="000001", name="平安银行", raw_sentiment=ctx.raw_sentiment,
            sufficiency=DataSufficiency(),
        ))
        assert enriched.sufficiency is not None
        assert "过期缓存兜底" in enriched.sufficiency.sentiment.reason

    def test_cache_served_when_breaker_open(self, tmp_path, mocker):
        """断路器打开时，collect 仍从本地缓存返回健康数据"""
        from data.schemas import IndustryData
        pipe = self._make_pipeline(tmp_path, mocker)
        # 先写入健康缓存
        ind = IndustryData(symbol="000001", industry="银行", sector="金融",
                           peers=["600000"], top_peers=[])
        pipe._set_cache("000001", "industry", [ind])
        assert pipe._get_cached("000001", "industry") is not None
        # 手动打开断路器
        for _ in range(3):
            pipe._breaker.record_failure("000001", "industry")
        assert pipe._breaker.is_open("000001", "industry")
        # 断路器打开期间 collect：应命中缓存返回，而非被断路器屏蔽为 None
        ctx = pipe.collect("000001", "平安银行", data_types=["industry"])
        assert ctx.industry_data is not None
        assert ctx.industry_data.industry == "银行"
        # 缓存命中走 record_success，断路器被重置
        assert not pipe._breaker.is_open("000001", "industry")


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

    def test_analysis_results_have_scores(self, mocker, tmp_path):
        """分析结果应有配置驱动的分数"""
        from analysis.financial import FinancialAnalyzer
        from analysis.industry import IndustryAnalyzer
        from analysis.sentiment import SentimentAnalyzer
        from analysis.technical import TechnicalAnalyzer
        from analysis.valuation import ValuationAnalyzer
        from core.pipeline import Pipeline
        from core.registry import Registry
        from data.akshare import AkShareAdapter
        from utils.config import Config

        financials = [
            FinancialData(
                symbol="000001", fiscal_quarter=date(year, quarter, 28),
                revenue=revenue, net_profit=profit, total_assets=500e9,
                total_equity=45e9, operating_cash_flow=12e9, roe=0.12,
            )
            for year, quarter, revenue, profit in [
                (2026, 3, 14e9, 2.8e9), (2025, 12, 52e9, 10e9),
                (2025, 9, 38e9, 7.5e9), (2025, 6, 25e9, 5e9),
            ]
        ]

        def fetch(_self, symbol, **kwargs):
            data_type = kwargs["data_type"]
            if data_type == "financial":
                return financials
            if data_type == "price":
                return [PriceData(
                    symbol=symbol, trade_date=date(2026, 8, 27), open=10,
                    high=11, low=9.5, close=10.5, volume=1_000_000,
                )]
            if data_type == "valuation":
                return [ValuationData(
                    symbol=symbol, date=date(2026, 8, 27), pe_ttm=7.5, pb=0.85,
                )]
            if data_type == "industry":
                return [IndustryData(
                    symbol=symbol, industry="银行", sector="金融", peers=["600036"],
                )]
            if data_type == "news":
                return [NewsData(
                    symbol=symbol, date=date(2026, 8, 27), headlines=["业绩增长"],
                )]
            return []

        mocker.patch.object(AkShareAdapter, "fetch", new=fetch)

        reg = Registry()
        reg.register_data_source(AkShareAdapter())
        reg.register_analysis_module(FinancialAnalyzer())
        reg.register_analysis_module(ValuationAnalyzer())
        reg.register_analysis_module(IndustryAnalyzer())
        reg.register_analysis_module(TechnicalAnalyzer())
        reg.register_analysis_module(SentimentAnalyzer())

        pipeline = Pipeline(
            registry=reg, config=Config(config_dir=tmp_path), llm_enabled=False,
        )
        results, _, ctx = pipeline.run("000001", "平安银行")

        assert len(results) == 5
        assert ctx.sw_industry != ""
        # 至少有一个维度有分数
        scored = [r for r in results if r.score is not None]
        assert len(scored) > 0

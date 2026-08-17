"""管道端到端集成测试"""


class TestPipelineIntegration:
    def test_pipeline_runs_without_error(self):
        """验证完整管道：采集 → 充实 → 分析（不含 LLM 调用）"""
        from analysis.financial import FinancialAnalyzer
        from analysis.valuation import ValuationAnalyzer
        from core.pipeline import Pipeline
        from core.registry import Registry
        from data.akshare import AkShareAdapter
        from utils.config import Config

        reg = Registry()
        reg.register_data_source(AkShareAdapter())
        reg.register_analysis_module(FinancialAnalyzer())
        reg.register_analysis_module(ValuationAnalyzer())

        config = Config()
        pipeline = Pipeline(registry=reg, config=config, llm_enabled=False)

        result = pipeline.run("000001", "平安银行", dimension="financial")
        results, _, ctx = result

        assert len(results) > 0
        assert results[0].dimension == "financial"
        assert hasattr(results[0], "score")
        # 验证充实层已运行（sufficiency 应被填充）
        assert ctx.sufficiency is not None
        assert ctx.sufficiency.price is not None

    def test_pipeline_all_dimensions(self):
        """完整五维度分析（不含 LLM 调用）"""
        from analysis.financial import FinancialAnalyzer
        from analysis.industry import IndustryAnalyzer
        from analysis.sentiment import SentimentAnalyzer
        from analysis.technical import TechnicalAnalyzer
        from analysis.valuation import ValuationAnalyzer
        from core.pipeline import Pipeline
        from core.registry import Registry
        from data.akshare import AkShareAdapter
        from utils.config import Config

        reg = Registry()
        reg.register_data_source(AkShareAdapter())
        reg.register_analysis_module(FinancialAnalyzer())
        reg.register_analysis_module(TechnicalAnalyzer())
        reg.register_analysis_module(ValuationAnalyzer())
        reg.register_analysis_module(IndustryAnalyzer())
        reg.register_analysis_module(SentimentAnalyzer())

        config = Config()
        pipeline = Pipeline(registry=reg, config=config, llm_enabled=False)

        results, _, _ = pipeline.run("000001", "平安银行")

        assert len(results) == 5
        dimensions = {r.dimension for r in results}
        assert dimensions == {"financial", "technical", "valuation", "industry", "sentiment"}
        # 每个维度都应该有 score 字段
        for r in results:
            assert hasattr(r, "score")

"""报告与真实模型调用入口的 P0 契约回归。"""
import pytest

from analysis.valuation import ValuationAnalyzer
from core.pipeline import Pipeline
from core.registry import Registry
from data.schemas import AnalysisResult
from llm.base import LLMBackend
from report.builder import ReportBuilder, _md_table
from tests.analysis.test_valuation_validity import CONFIG, context
from tests.report.test_peer_comparison import comparison_sample
from utils.config import Config


@pytest.mark.parametrize("provider", ["openai", "claude"])
@pytest.mark.parametrize("market", ["a-shares", "hk"])
def test_actual_prompt_contains_formatted_facts_and_invalid_pe_reason(tmp_path, provider, market):
    class CapturingBackend(LLMBackend):
        prompt = ""

        @property
        def model_name(self):
            return "test"

        def generate(self, prompt, **kwargs):
            self.prompt = prompt
            return "测试解读"

    ctx = context(pe=-32.03, pb=2, ps=None)
    ctx.market = market
    result = ValuationAnalyzer().analyze(ctx, CONFIG if market == "a-shares" else None)
    financial = AnalysisResult(dimension="financial", status="partial", summary="财报快照", score=2 if market == "a-shares" else None,
                               metrics={"revenue": 3000000, "roe": -0.0956,
                                   "annual_roe": {"value": 0.15, "period_start": "2025-01-01",
                                       "period_end": "2025-12-31", "source": "年度ROE来源", "basis": "完整年度加权"}})
    industry = AnalysisResult(dimension="industry", status="partial", summary="同期经营比较",
                              metrics={"comparison": comparison_sample()})
    config = Config(config_dir=tmp_path)
    config.data["llm"]["provider"] = provider
    registry = Registry()
    backend = CapturingBackend()
    registry.register_llm_backend(backend, provider=provider)
    output = Pipeline(registry, config)._generate_commentary(ctx.symbol, ctx.name, [result, financial, industry], context=ctx)
    assert output == {"bulk": "测试解读"}
    assert "-150.6" not in backend.prompt
    assert "-32.03" in backend.prompt
    assert "不适用于高低估判断" in backend.prompt
    assert "300.00 万" in backend.prompt
    assert "-9.56%" in backend.prompt
    assert '"annual_roe": {"value": "15.00%"' in backend.prompt
    assert "年度ROE来源" in backend.prompt
    assert "12.50%" in backend.prompt
    assert "1250.00%" not in backend.prompt
    assert financial.metrics["annual_roe"]["value"] == 0.15
    assert financial.metrics["roe"] == -0.0956
    assert industry.metrics["comparison"]["periods"]["annual"]["metrics"][0]["target"] == 12.5
    assert ("金额币种未核实" if market == "hk" else "币种未记录") in backend.prompt


def test_tables_scores_and_original_screenshot_values():
    ctx = context(pe=-32.03, pb=2, ps=None)
    valuation = ValuationAnalyzer().analyze(ctx, CONFIG)
    financial = AnalysisResult(dimension="financial", status="partial", summary="财报快照", metrics={
        "revenue": 2623000000, "net_profit": -382000000, "roe": -0.0956, "gross_margin": .161,
        "roe_trend": [{"quarter": "2026-06-30", "roe": -0.0956}],
    })
    text = ReportBuilder().build(ctx.symbol, ctx.name, [valuation, financial], {}, no_llm=True,
                                 base_score=1.5999999999999996, final_score=1.5999999999999996)
    for value in ("26.23 亿", "-3.82 亿", "-9.56%", "16.10%", "1.6/10", "不适用"):
        assert value in text
    for bad in ("1.5999999999999996", "2623000000", "-150.6"):
        assert bad not in text
    table = _md_table([{"symbol": "000001", "market_cap": 3000000, "pb": 1.234}],
                      {"代码": "symbol", "市值": "market_cap", "PB": "pb"})
    assert "000001" in table and "300.00 万" in table and "1.23" in table

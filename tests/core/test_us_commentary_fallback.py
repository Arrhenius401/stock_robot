"""美股真实解读入口的失败回退不能改写量化事实。"""
from copy import deepcopy

import pytest

from core.pipeline import Pipeline
from core.registry import Registry
from data.schemas import AnalysisContext, AnalysisResult
from llm.base import LLMBackend
from report.payload import build_stock_payload
from utils.config import Config


class FailedBackend(LLMBackend):
    def __init__(self, raises):
        self.raises = raises

    @property
    def model_name(self):
        return "failure-probe"

    def generate(self, prompt, **kwargs):
        if self.raises:
            raise TimeoutError("隔离验收超时")
        return "（LLM 分析暂时不可用：模型未返回正文）"


@pytest.mark.parametrize("raises", [True, False])
def test_us_commentary_failure_preserves_scores_and_visible_fallback(tmp_path, raises):
    config = Config(tmp_path)
    config.data["analysis"]["extra_enabled"] = True
    registry = Registry()
    registry.register_llm_backend(FailedBackend(raises), provider="openai")
    pipeline = Pipeline(registry, config)
    ctx = AnalysisContext(symbol="AAPL", name="苹果", market="us", extra_analysis_enabled=True)
    values = [AnalysisResult(dimension="financial", status="partial", score=8,
                             summary="已采集财务事实", metrics={"roe": .12}),
              AnalysisResult(dimension="industry", status="unavailable", summary="同行不足", score=None)]
    before = deepcopy(values)
    commentary = pipeline._generate_commentary(ctx.symbol, ctx.name, values, context=ctx)
    payload = build_stock_payload(ctx.symbol, ctx.name, values, commentary, ctx)
    assert payload["commentary_status"] == "failed"
    assert "程序生成的量化摘要" in payload["commentary"]
    assert payload["score"]["final"] == 8
    assert payload["signal"] is None
    assert payload["data_quality"]["signal_allowed"] is False
    assert values == before

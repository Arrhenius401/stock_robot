"""真实批量提示词中的同行单位与不计分覆盖语义。"""
import json
from copy import deepcopy

import pytest

from core.pipeline import Pipeline
from core.registry import Registry
from data.schemas import AnalysisContext, AnalysisResult
from llm.base import LLMBackend
from utils.config import Config


class CapturingBackend(LLMBackend):
    prompt = ""

    @property
    def model_name(self):
        return "test"

    def generate(self, prompt, **kwargs):
        self.calls = getattr(self, "calls", 0) + 1
        self.prompt = prompt
        return "事实解读"


def _comparison():
    return {"version": 1, "scope": "同业", "peer_count": 5, "valuation": {
        "metrics": [{"key": "pe_ttm", "unit": "multiple", "target": 20, "median": 25, "percentile": 30, "sample_count": 5}],
        "peers": [{"symbol": "000001", "pe_ttm": 25}],
    }, "periods": {"annual": {"label": "2025全年", "start": "2025-01-01", "end": "2025-12-31",
        "metrics": [{"key": key, "unit": "percent", "target": value, "median": value, "percentile": 50, "sample_count": 5}
                    for key, value in (("roe", 12.5), ("net_margin", 0), ("revenue_growth_yoy", -3))],
        "peers": [{"symbol": "000001", "roe": 12.5, "net_margin": 0, "revenue_growth_yoy": -3,
                   "start": "2025-01-01", "end": "2025-12-31"}]}, "latest": {}}}


@pytest.mark.parametrize("provider", ["openai", "claude"])
@pytest.mark.parametrize("market", ["a-shares", "hk"])
@pytest.mark.parametrize("status", ["ok", "partial", "unavailable"])
def test_actual_prompt_keeps_comparison_units_and_industry_availability(tmp_path, provider, market, status):
    comparison = _comparison()
    original = deepcopy(comparison)
    industry = AnalysisResult(dimension="industry", status=status, score=None,
        summary="2025全年ROE12.50%，营收同比-3.00%", score_detail="有效指标不足，暂无行业分",
        metrics={"peer_count": 5, "comparison": comparison})
    financial = AnalysisResult(dimension="financial", status="ok", score=8 if market == "a-shares" else None,
        summary="财务快照", metrics={"roe": 0.125, "revenue": 3000000})
    ctx = AnalysisContext(symbol="000333" if market == "a-shares" else "00700.HK", name="样例", market=market)
    config = Config(config_dir=tmp_path)
    config.data["llm"]["provider"] = provider
    registry = Registry()
    backend = CapturingBackend()
    registry.register_llm_backend(backend, provider=provider)
    assert Pipeline(registry, config)._generate_commentary(ctx.symbol, ctx.name, [financial, industry], context=ctx) == {"bulk": "事实解读"}
    prompt = backend.prompt
    assert backend.calls == 1
    assert "## 3.多风格观察视角" in prompt
    assert "## 4.综合" in prompt
    start = prompt.index('[{"dimension"')
    facts, _ = json.JSONDecoder().raw_decode(prompt[start:])
    assert facts[0]["metrics"]["roe"] == "12.50%"
    data = facts[1]["metrics"]["comparison"]
    annual = data["periods"]["annual"]
    assert annual["metrics"][0]["target"] == "12.50%"
    assert annual["metrics"][0]["median"] == "12.50%"
    assert annual["metrics"][0]["sample_count"] == 5
    assert annual["peers"][0]["roe"] == "12.50%"
    assert annual["peers"][0]["net_margin"] == "0.00%"
    assert annual["peers"][0]["revenue_growth_yoy"] == "-3.00%"
    assert data["valuation"]["metrics"][0]["target"] == "20.00"
    assert "1250" not in prompt and "-300" not in prompt
    assert facts[1]["summary"] == industry.summary
    assert comparison == original
    assert "行业分只采用已提供的同期经营比较计算结果" in prompt
    if market == "a-shares":
        covered = prompt.split("已覆盖维度：", 1)[1].splitlines()[0]
        missing = prompt.split("缺失维度：", 1)[1].splitlines()[0]
        assert ("行业对比" in covered) == (status != "unavailable")
        assert ("行业对比" in missing) == (status == "unavailable")
        section = prompt.split("### 行业对比", 1)[1].split("### 舆情风险", 1)[0]
        if status != "unavailable":
            assert "数据不足，跳过此维度" not in section
            assert "暂无评分" in section
            assert industry.summary in section

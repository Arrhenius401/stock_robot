"""公共模板的三市场规则与实际管道提示词回归。"""
import pytest

from core.pipeline import Pipeline
from core.registry import Registry
from data.schemas import AnalysisContext, AnalysisResult, IndustryData
from llm.base import LLMBackend
from utils.config import Config


class CaptureBackend(LLMBackend):
    prompt = ""

    @property
    def model_name(self):
        return "capture"

    def generate(self, prompt, **kwargs):
        self.prompt = prompt
        return "可核实事实解读"


@pytest.mark.parametrize("provider", ["openai", "claude"])
@pytest.mark.parametrize("market", ["a-shares", "hk", "us"])
def test_pipeline_common_prompt_preserves_market_rules(tmp_path, provider, market):
    config = Config(config_dir=tmp_path)
    config.data["analysis"]["extra_enabled"] = True
    config.data["llm"]["provider"] = provider
    registry = Registry()
    backend = CaptureBackend()
    registry.register_llm_backend(backend, provider=provider)
    ctx = AnalysisContext(symbol="AAPL", name="样例", market=market, extra_analysis_enabled=True,
        sw_industry="电子", industry_data=IndustryData(symbol="AAPL", industry="系统软件", sector=""))
    values = [AnalysisResult(dimension="financial", status="ok", score=8, summary="快照",
        metrics={"roe": .12, "free_cash_flow": 100}),
        AnalysisResult(dimension="industry", status="partial", summary="分类和经营样本",
            industry_note="不是 GICS/申万分类", metrics={"industry_basis": "不是 GICS/申万分类", "peer_count": 3})]
    result = Pipeline(registry, config)._generate_commentary(ctx.symbol, ctx.name, values, context=ctx)
    assert result == {"bulk": "可核实事实解读"}
    prompt = backend.prompt
    assert "## 1.核心判断" in prompt
    assert "不重新计算分数" in prompt
    assert "不得将 TTM 现金流与单季利润直接比较" in prompt
    assert "半年度或累计期间不得称作季度" in prompt
    assert "不得从价格、估值分位或新闻相关性推断资金流向" in prompt
    assert "新闻中的目标价、仓位及买卖动作不得复述" in prompt
    assert "不得把历史低分位或同行折价直接表述" in prompt
    assert "volume_ratio 是近5日平均成交量与此前20日平均成交量的比值" in prompt
    assert "不能把缺项称作低分的原因" in prompt
    assert "严格禁止词汇" in prompt
    assert "禁止自行生成交易信号" in prompt
    assert "12.00%" in prompt
    if market == "us":
        assert "东财美股 F10 行业（BELONG_INDUSTRY）" in prompt
        assert "USD" in prompt and "美国会计准则" in prompt and "财季" in prompt
        assert "舆情本期未计分" in prompt
        for word in ("GICS", "申万", "HSICS", "FCF"):
            assert word not in prompt
    elif market == "hk":
        assert "恒生HSICS" in prompt and "金额币种未核实" in prompt
        assert "不能把半年度/年度数据当季度" in prompt
    else:
        assert "申万一级行业" in prompt

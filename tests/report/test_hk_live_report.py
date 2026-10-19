"""真实 QQ 失败报告与港股无评分解读回归。"""
from pathlib import Path

import pytest

from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    DimensionSufficiency,
    SufficiencyLevel,
)
from report.payload import build_stock_payload
from report.quality import build_data_quality
from report.scoring import build_report


def hk_context():
    ctx = AnalysisContext(symbol="00700.HK", name="腾讯控股", market="hk")
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.PARTIAL, sample_count=5)
    return ctx


def test_real_qq_failure_sample_and_corrected_market_report():
    original = (Path(__file__).parents[1] / "resources/report/qq-00700-unavailable-report.md").read_text(encoding="utf-8")
    assert "市场：A 股" in original
    assert "2241期财报" in original
    ctx = hk_context()
    result = AnalysisResult(dimension="financial", status="partial", summary="港股报告期", metrics={"latest_quarter": "2026-06-30", "revenue": 396431000000})
    quality = build_data_quality(ctx, [result])
    assert quality["has_analysis_data"]
    assert not quality["has_scored_data"]
    payload = build_stock_payload(ctx.symbol, ctx.name, [result], {"bulk": "基于营业收入的解读"}, ctx)
    assert payload["commentary_status"] == "ok"
    assert payload["score"]["final"] is None
    assert payload["signal"] is None
    text = build_report(ctx.symbol, ctx.name, [result], {"bulk": "基于营业收入的解读"}, ctx)
    assert "市场：港股" in text
    assert "基于营业收入的解读" in text
    assert "原币" in text
    assert "不适用" in text
    assert "申万行业待补全" not in text
    assert "最新财报季度" not in text


@pytest.mark.parametrize("metrics,status", [({"latest_quarter": "2026-06-30", "revenue": None}, "partial"), ({"revenue": 1}, "unavailable"), ({"revenue": float("nan")}, "partial"), ({"sample_count": 2241}, "partial")])
def test_hk_empty_or_failed_facts_do_not_enable_model(metrics, status):
    ctx = hk_context()
    result = AnalysisResult(dimension="financial", status=status, summary="", metrics=metrics)
    assert not build_data_quality(ctx, [result])["has_analysis_data"]
    assert build_stock_payload(ctx.symbol, ctx.name, [result], {"bulk": "禁止出现"}, ctx)["commentary_status"] == "insufficient"


def test_a_share_unscored_metrics_keep_existing_model_gate():
    ctx = AnalysisContext(symbol="000001", name="测试")
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.PARTIAL, sample_count=5)
    result = AnalysisResult(dimension="financial", status="partial", summary="", metrics={"revenue": 396431000000})
    assert not build_data_quality(ctx, [result])["has_analysis_data"]
    assert build_stock_payload(ctx.symbol, ctx.name, [result], {"bulk": "禁止出现"}, ctx)["commentary_status"] == "insufficient"


@pytest.mark.parametrize("provider", ["openai", "claude"])
def test_hk_shared_prompt_uses_real_metrics_without_scores(tmp_path, provider):
    from core.pipeline import Pipeline
    from core.registry import Registry
    from llm.base import LLMBackend
    from utils.config import Config

    class CapturingBackend(LLMBackend):
        prompt = ""

        @property
        def model_name(self):
            return "test"

        def generate(self, prompt, **kwargs):
            self.prompt = prompt
            return "港股事实解读"

    ctx = hk_context()
    result = AnalysisResult(dimension="financial", status="partial", summary="半年度报告", metrics={"revenue": 396431000000, "latest_quarter": "2026-06-30"})
    config = Config(config_dir=tmp_path)
    config.data["llm"]["provider"] = provider
    registry = Registry()
    backend = CapturingBackend()
    registry.register_llm_backend(backend, provider=provider)
    pipe = Pipeline(registry, config)
    assert pipe._generate_commentary(ctx.symbol, ctx.name, [result], context=ctx) == {"bulk": "港股事实解读"}
    assert "396431000000" in backend.prompt
    assert "2026-06-30" in backend.prompt
    assert "原币" in backend.prompt
    assert "禁止" in backend.prompt
    assert "不适用" in backend.prompt
    backend.prompt = ""
    result.metrics = {"latest_quarter": "2026-06-30", "revenue": None}
    assert pipe._generate_commentary(ctx.symbol, ctx.name, [result], context=ctx) == {}
    assert not backend.prompt

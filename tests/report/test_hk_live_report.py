"""真实 QQ 失败报告、港股有效事实及评分信号边界回归。"""
from datetime import date
from pathlib import Path
from typing import Literal

import pytest

from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    DimensionSufficiency,
    FinancialData,
    SufficiencyLevel,
)
from report.payload import build_stock_payload
from report.quality import build_data_quality
from report.scoring import build_report
from report.signal import SignalConfig


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
    assert "金额币种未核实" in text
    assert "five-dimension-v1" in text
    assert "本期港股不提供交易信号" in text
    assert "申万行业待补全" not in text
    assert "最新财报季度" not in text


@pytest.mark.parametrize("metrics,status", [({"latest_quarter": "2026-06-30", "revenue": None}, "partial"), ({"revenue": 1}, "unavailable"), ({"revenue": float("nan")}, "partial"), ({"sample_count": 2241}, "partial")])
def test_hk_empty_or_failed_facts_do_not_enable_model(metrics, status):
    ctx = hk_context()
    result = AnalysisResult(dimension="financial", status=status, summary="", metrics=metrics)
    assert not build_data_quality(ctx, [result])["has_analysis_data"]
    assert build_stock_payload(ctx.symbol, ctx.name, [result], {"bulk": "禁止出现"}, ctx)["commentary_status"] == "insufficient"


def test_a_share_unscored_metrics_allow_fact_based_commentary_without_signal():
    ctx = AnalysisContext(symbol="000001", name="测试")
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.PARTIAL, sample_count=5)
    result = AnalysisResult(dimension="financial", status="partial", summary="", metrics={"revenue": 396431000000})
    assert build_data_quality(ctx, [result])["has_analysis_data"]
    payload = build_stock_payload(ctx.symbol, ctx.name, [result], {"bulk": "依据有限事实解读"}, ctx)
    assert payload["commentary_status"] == "ok"
    assert payload["score"]["final"] is None
    assert payload["signal"] is None


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
    cached = FinancialData.model_validate({"symbol": "00700.HK", "fiscal_quarter": "2026-06-30",
        "report_currency": "CNY", "gross_margin_basis": "计算值：同期间披露毛利÷营运收入；未换汇"})
    result = AnalysisResult(dimension="financial", status="partial", summary="半年度报告", metrics={
        "revenue": 396431000000, "latest_quarter": "2026-06-30", "gross_margin_basis": cached.gross_margin_basis})
    config = Config(config_dir=tmp_path)
    config.data["llm"]["provider"] = provider
    registry = Registry()
    backend = CapturingBackend()
    registry.register_llm_backend(backend, provider=provider)
    pipe = Pipeline(registry, config)
    assert pipe._generate_commentary(ctx.symbol, ctx.name, [result], context=ctx) == {"bulk": "港股事实解读"}
    assert "3,964.31 亿" in backend.prompt
    assert "2026-06-30" in backend.prompt
    assert "金额币种未核实" in backend.prompt
    assert "保持财报原币，未做换汇" not in backend.prompt
    assert "；未换汇" not in backend.prompt
    assert "禁止" in backend.prompt
    assert "不适用" in backend.prompt
    backend.prompt = ""
    result.metrics = {"latest_quarter": "2026-06-30", "revenue": None}
    assert pipe._generate_commentary(ctx.symbol, ctx.name, [result], context=ctx) == {}
    assert not backend.prompt


def test_hk_financial_source_and_selection_note_are_visible():
    ctx = hk_context()
    ctx.financial_data = [FinancialData(
        symbol="00700.HK",
        fiscal_quarter=date(2026, 6, 30),
        revenue=1.0,
        financial_source="东方财富港股财报",
        financial_selection_status="complete",
        financial_selection_note="完整来源包",
    )]
    result = AnalysisResult(
        dimension="financial", status="partial", summary="港股报告期",
        metrics={"revenue": 1.0, "latest_quarter": "2026-06-30"},
    )
    quality = build_data_quality(ctx, [result])
    financial = next(item for item in quality["dimensions"] if item["key"] == "financial")
    fields = {field["label"]: field["value"] for field in financial["fields"]}
    assert fields["数据来源"] == "东方财富港股财报"
    assert fields["财务包说明"] == "完整来源包"


def test_hk_valid_scores_enable_total_without_trade_signal_or_position():
    ctx = hk_context()
    for key in ("financial", "price", "valuation"):
        setattr(ctx.sufficiency, key, DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT))
    facts: list[tuple[Literal["financial", "technical", "valuation"], str]] = [
        ("financial", "revenue"), ("technical", "latest_close"), ("valuation", "pb")]
    results = [AnalysisResult(dimension=key, status="ok", summary="有效评分", score=8,
                             metrics={metric: 1}) for key, metric in facts]
    quality = build_data_quality(ctx, results)
    assert quality["has_scored_data"]
    assert quality["coverage"] == 75.0
    assert quality["available_dimensions"] == 3
    assert quality["applicable_dimensions"] == 5
    assert not quality["signal_allowed"]
    payload = build_stock_payload(ctx.symbol, ctx.name, results, {"bulk": "有效港股数据解读"}, ctx,
                                  signal_cfg=SignalConfig())
    assert payload["score"]["final"] == 8.0
    assert payload["signal"] is None
    assert payload["commentary_status"] == "ok"

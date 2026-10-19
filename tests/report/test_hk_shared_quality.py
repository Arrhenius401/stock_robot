"""港股适用维度覆盖与完整性分离，不继承 A 股评分权重。"""
from typing import Literal

import pytest

from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    DimensionSufficiency,
    SufficiencyLevel,
)
from report.quality import build_data_quality
from report.scoring import build_report


def context():
    ctx = AnalysisContext(symbol="00700.HK", name="腾讯", market="hk")
    for key in ("financial", "price", "valuation"):
        setattr(ctx.sufficiency, key, DimensionSufficiency(level=SufficiencyLevel.PARTIAL))
    return ctx


@pytest.mark.parametrize("count", range(4))
def test_coverage_counts_only_applicable_available_dimensions(count):
    ctx = context()
    facts: list[tuple[Literal["financial", "technical", "valuation"], str]] = [
        ("financial", "revenue"), ("technical", "latest_close"), ("valuation", "pe_ttm")]
    results = [AnalysisResult(dimension=dimension, status="partial", summary="部分字段缺失",
                              metrics={key: 1 if index < count else None})
               for index, (dimension, key) in enumerate(facts)]
    quality = build_data_quality(ctx, results)
    assert quality["coverage"] == round(count / 3 * 100, 1)
    assert quality["available_dimensions"] == count
    assert quality["applicable_dimensions"] == 3
    assert quality["status"] == ("partial" if count else "unavailable")
    assert not quality["signal_allowed"]
    assert not quality["has_scored_data"]
    for key in ("industry", "sentiment"):
        dimension = next(item for item in quality["dimensions"] if item["key"] == key)
        assert dimension["status"] == "not_applicable"
    assert not any(item.startswith(("行业：", "舆情：")) for item in quality["pending_verifications"])


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), "12"])
def test_non_facts_never_increase_coverage(value):
    quality = build_data_quality(context(), [AnalysisResult(
        dimension="financial", status="partial", summary="", metrics={"revenue": value})])
    assert quality["coverage"] == 0
    assert not quality["has_analysis_data"]


def test_unavailable_residual_values_are_not_counted():
    quality = build_data_quality(context(), [AnalysisResult(
        dimension="technical", status="unavailable", summary="不足", metrics={"latest_close": 417.8})])
    assert quality["coverage"] == 0


def test_markdown_shows_dimension_count_and_not_applicable():
    ctx = context()
    results = [AnalysisResult(dimension="financial", status="partial", summary="利润为负",
                              metrics={"net_profit": -10})]
    text = build_report(ctx.symbol, ctx.name, results, {}, ctx, no_llm=True)
    assert "可用维度覆盖：1/3（33.3%）" in text
    assert "行业 · 不适用" in text
    assert "舆情 · 不适用" in text

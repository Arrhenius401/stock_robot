"""指数综合研判的确定性结构与证据边界。"""
from datetime import date

import pytest

from data.schemas import (
    AnalysisResult,
    AnalysisTarget,
    IndexAnalysisContext,
    IndexPriceData,
)
from index.build_single import IndexReportBuilder, render_index_report_markdown


@pytest.mark.parametrize(("market", "style", "absent"), [
    ("a-shares", "broad", set()),
    ("a-shares", "sector", {"macro"}),
    ("a-shares", "strategy", {"macro", "capital", "sentiment"}),
    ("a-shares", None, {"macro", "capital"}),
    ("hk", "overseas", {"strategy"}),
    ("us", "overseas", {"capital", "strategy"}),
    ("overseas", "overseas", {"capital", "macro", "sentiment"}),
])
def test_index_research_uses_profile_and_frozen_facts(market, style, absent):
    target = AnalysisTarget(target_type="index", symbol="000300", name="样本指数", market=market, index_style=style)
    ctx = IndexAnalysisContext(target=target, requested_instrument={"symbol": "510300", "name": "样本ETF"})
    ctx.price_data = [IndexPriceData(
        symbol=target.symbol, trade_date=date(2026, 11, 17), open=100, high=103, low=99, close=102, volume=0,
    )]
    results = [
        AnalysisResult(dimension="index_technical", status="partial", summary="OHLC 不完整",
                       metrics={"latest_close": 102, "ma_5": 100, "notes": ["OHLC 不完整"]}),
        AnalysisResult(dimension="index_performance", status="partial", summary="历史不足",
                       metrics={"sample_count": 1, "windows": [{"notes": ["历史不足完整窗口"]}]}),
    ]

    report = IndexReportBuilder().build(ctx, results)

    research = report.index_research
    assert research is not None
    assert research["model"] is None
    assert research["version"] == 2
    assert "510300" in str(research["positioning"])
    keys = {row["key"].removeprefix("index_") for row in research["dimensions"]}
    assert not keys & absent
    assert "performance" not in keys
    assert "index_performance" in research["evidence"]
    assert {row["id"] for row in research["risk_candidates"]} >= {"technical.incomplete_ohlc", "performance.history"}
    markdown = render_index_report_markdown(report)
    assert markdown.index("### 指数定位") < markdown.index("### 维度解读") < markdown.index("### 解读限制")
    assert "### 核心结论" not in markdown

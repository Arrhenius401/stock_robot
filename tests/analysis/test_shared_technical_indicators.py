"""跨市场技术事实与有效子项评分归一回归。"""

from datetime import date, timedelta
from statistics import mean

import pytest

from analysis.scorers.general import GeneralScorer, calculate_macd
from analysis.technical import TechnicalAnalyzer
from data.schemas import (
    AnalysisContext,
    DataSufficiency,
    DimensionSufficiency,
    PriceData,
    SufficiencyLevel,
)


def make_context(count: int, market: str = "a-shares") -> AnalysisContext:
    symbol = "00700.HK" if market == "hk" else "000001"
    return AnalysisContext(
        symbol=symbol, name="测试", market=market,
        price_data=[
            PriceData(
                symbol=symbol, trade_date=date(2026, 1, 1) + timedelta(days=i),
                open=10, high=30, low=1, close=10 + (i % 7) * .2 + i * .1,
                volume=100 + i * 7,
            ) for i in range(count)
        ],
        sufficiency=DataSufficiency(price=DimensionSufficiency(
            level=SufficiencyLevel.SUFFICIENT, sample_count=count, score_weight=1,
        )),
    )


@pytest.mark.parametrize("count", [0, 5, 20, 25, 26, 33, 34, 60])
def test_public_metrics_require_complete_windows_and_match_across_markets(count):
    a_ctx = make_context(count)
    hk_ctx = make_context(count, "hk")
    a_result = TechnicalAnalyzer().analyze(a_ctx)
    hk_result = TechnicalAnalyzer().analyze(hk_ctx)
    assert a_result.metrics == hk_result.metrics
    assert hk_result.score is None
    assert hk_result.risk_flags == []
    if not count:
        assert hk_result.status == "unavailable"
        assert hk_result.metrics == {}
        return
    closes = [p.close for p in a_ctx.price_data or []]
    volumes = [p.volume for p in a_ctx.price_data or []]
    expected: dict[str, float | None] = {"latest_close": closes[-1]}
    for window in (5, 20, 60):
        expected[f"ma_{window}"] = mean(closes[-window:]) if count >= window else None
    expected["volume_ratio"] = mean(volumes[-5:]) / mean(volumes[-25:-5]) if count >= 25 else None
    macd = calculate_macd(closes)
    expected.update({
        "macd_dif": macd.dif if count >= 34 else None,
        "macd_dea": macd.dea if count >= 34 else None,
        "macd_bar": macd.bar if count >= 34 else None,
    })
    assert hk_result.metrics == expected


def test_hk_uses_shared_scorer_when_given_its_selected_config(mocker):
    get_scorer = mocker.patch("analysis.financial.FinancialAnalyzer._get_scorer")
    get_scorer.return_value.score_technical.return_value = (7.0, "有效子项评分", [])
    get_scorer.return_value._industry_note.return_value = ""
    result = TechnicalAnalyzer().analyze(make_context(60, "hk"), {"technical": {}})
    get_scorer.assert_called_once_with({"technical": {}})
    assert result.score == 7
    assert result.score_detail == "有效子项评分"
    assert result.metrics["ma_60"] is not None


def test_zero_volume_is_missing_ratio_not_invented_neutral_value():
    ctx = make_context(60, "hk")
    for p in ctx.price_data or []:
        p.volume = 0
    result = TechnicalAnalyzer().analyze(ctx)
    assert result.metrics["volume_ratio"] is None
    score, detail, _ = GeneralScorer({}, {}).score_technical(ctx)
    assert score is not None
    assert "量比 1.00" not in detail
    assert "量价配合数据不足" in detail


@pytest.mark.parametrize(("count", "score", "detail"), [
    (0, None, "行情数据不可用"),
    (5, None, "没有可用技术计分子项"),
    (20, 50/7, "放量上涨（量比 1.47），得 3/3 分"),
    (24, 50/7, "放量上涨（量比 1.52），得 3/3 分"),
    (25, 50/7, "放量上涨（量比 1.53），得 3/3 分"),
    (26, 50/7, "MACD数据不足或权重无效，不计分"),
    (33, 50/7, "MACD数据不足或权重无效，不计分"),
    (34, 8.0, "MACD 多头，得 3/3 分"),
    (60, 8.0, "MACD 空头，得 1/3 分"),
])
def test_technical_scoring_keeps_bands_and_normalizes_available_items(count, score, detail):
    actual, explanation, risks = GeneralScorer({}, {}).score_technical(make_context(count))
    assert actual == pytest.approx(score) if score is not None else actual is None
    assert detail in explanation
    assert risks == []
    if score is not None:
        assert "按原相对权重折算" in explanation


@pytest.mark.parametrize("count", [0, 5, 20, 25, 26, 33])
def test_legacy_macd_import_and_short_history_defaults(count):
    from analysis.technical_indicators import calculate_macd as shared_macd

    closes = [p.close for p in make_context(count).price_data or []]
    assert shared_macd is calculate_macd
    value = calculate_macd(closes)
    assert value.dea == value.dif
    assert value.bar == 0


@pytest.mark.parametrize(("recent_volume", "label"), [
    (79, "量价一般"), (80, "横盘缩量"), (120, "横盘缩量"), (121, "量价一般"),
])
def test_volume_score_keeps_inclusive_neutral_ratio_bounds(recent_volume, label):
    ctx = make_context(25)
    for i, p in enumerate(ctx.price_data or []):
        p.close = 100
        p.volume = recent_volume if i >= 20 else 100
    config = {"technical": {
        "ma_structure": {"enabled": False}, "macd": {"enabled": False},
    }}
    score, detail, risks = GeneralScorer(config, {}).score_technical(ctx)
    assert score == pytest.approx(20/3)
    assert f"{label}（量比 {recent_volume / 100:.2f}），得 2/3 分" in detail
    assert risks == []


def test_bearish_ma_and_volume_risk_flags_are_unchanged():
    ctx = make_context(60)
    for i, p in enumerate(ctx.price_data or []):
        p.close = 100 - i
        p.volume = 200 if i >= 55 else 100
    result = GeneralScorer({"technical": {"macd": {"enabled": False}}}, {}).score_technical(ctx)
    assert result[0] == pytest.approx(20/7)
    assert "空头排列，得 1/4 分；放量下跌（量比 2.00），得 1/3 分" in result[1]
    assert result[2] == ["bearish_ma", "volume_bearish"]


def test_metrics_and_score_are_sorted_by_trade_date():
    ctx = make_context(60)
    expected = TechnicalAnalyzer().analyze(ctx).metrics
    expected_score = GeneralScorer({}, {}).score_technical(ctx)
    ctx.price_data = list(reversed(ctx.price_data or []))
    assert TechnicalAnalyzer().analyze(ctx).metrics == expected
    assert GeneralScorer({}, {}).score_technical(ctx) == expected_score


def test_zero_recent_volume_is_real_zero_ratio():
    ctx = make_context(25)
    for p in (ctx.price_data or [])[-5:]:
        p.volume = 0
    assert TechnicalAnalyzer().analyze(ctx).metrics["volume_ratio"] == 0
    assert "量比 0.00" in GeneralScorer({}, {}).score_technical(ctx)[1]

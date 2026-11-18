"""市场配置及共享报告入口的兼容契约。"""

import pytest

from data.schemas import AnalysisContext
from report.builder import build_disclaimer
from report.market_profile import profile_for, profile_for_label
from report.payload import build_stock_payload


@pytest.mark.parametrize(
    ("market", "label", "basis", "sentiment", "guard", "adapter"),
    [
        ("a-shares", "A 股", "quarterly_ttm", "scored", False, "ths"),
        ("hk", "港股", "report_period", "news_or_assessed", True, "f10_hk"),
        ("us", "美股", "ttm", "not_collected", True, "f10_us"),
    ],
)
def test_market_profile_and_payload(market, label, basis, sentiment, guard, adapter):
    profile = profile_for(market)
    assert profile is profile_for_label(label)
    assert profile.signal_policy == "data_gated"
    assert (profile.period_basis, profile.sentiment_policy, profile.currency_guard) == (basis, sentiment, guard)
    assert profile.business_profile == adapter
    assert list(profile.disclaimer) == build_disclaimer(hk=market == "hk", us=market == "us")
    ctx = AnalysisContext(symbol="test", name="测试", market=market)
    assert build_stock_payload(ctx.symbol, ctx.name, [], {}, ctx)["market_profile"] == {
        "key": market,
        "label": label,
        "industry_scheme": profile.industry_scheme,
        "period_basis": basis,
        "currency_guard": guard,
        "sentiment_policy": sentiment,
        "signal_policy": "data_gated",
        "business_profile_source": adapter,
    }


def test_unrecognized_market_is_rejected():
    with pytest.raises(ValueError):
        profile_for("gold")
    with pytest.raises(ValueError):
        profile_for_label("黄金")


@pytest.mark.parametrize(
    ("market", "dimension", "state", "summary", "reason", "applicable"),
    [
        ("us", "sentiment", "unknown", "", "not_collected", False),
        ("hk", "industry", "unknown", "非港股通标的，不参与港股行业对比", "market_policy", False),
        ("a-shares", "industry", "empty", "同行样本不足", "insufficient_sample", True),
        ("a-shares", "financial", "failed", "财务不可用", "collection_failed", True),
    ],
)
def test_dimension_state_distinguishes_missing_evidence(market, dimension, state, summary, reason, applicable):
    from data.schemas import AnalysisResult, DataCollectionRecord
    from report.capability import dimension_state

    ctx = AnalysisContext(symbol="test", name="测试", market=market)
    ctx.collection_records[dimension] = DataCollectionRecord(result_state=state)
    outcome = dimension_state(profile_for(market), AnalysisResult(
        dimension=dimension, status="unavailable", summary=summary), ctx)
    assert (outcome.applicable, outcome.coverage, outcome.reason) == (applicable, "none", reason)

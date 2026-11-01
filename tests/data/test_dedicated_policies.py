from datetime import datetime

from data.collection_policies import dedicated_policy_for
from data.hk_news import HK_TZ
from data.schemas import (
    HKNewsItem,
    HKNewsLink,
    HKNewsSnapshot,
    IndustryData,
    NewsData,
    ValuationData,
)

NOW = datetime(2026, 10, 27, 12, tzinfo=HK_TZ)


def hk_news(rows=1, errors=False):
    snapshot = HKNewsSnapshot(
        fetched_at=NOW, queries=["00700"], query_errors=["名称查询失败"] if errors else [],
        items=[HKNewsItem(title=f"消息{i}", published_at=NOW, links=[
            HKNewsLink(media="测试", url="https://example.com/news")]) for i in range(rows)],
    )
    return [NewsData(symbol="00700.HK", date=NOW.date(), hk_news=snapshot)]


def test_hk_news_policy_states():
    policy = dedicated_policy_for("hk", "news")
    assert policy is not None
    assert policy.classify("00700.HK", hk_news(), None, None, NOW) == "complete"
    assert policy.classify("00700.HK", hk_news(rows=0), None, None, NOW) == "empty"
    assert policy.classify("00700.HK", hk_news(errors=True), None, None, NOW) == "partial"
    assert policy.classify("00700.HK", [], None, None, NOW) == "failed"
    assert policy.reference_rows(hk_news(), NOW) is None


def test_valuation_policy_states():
    a = dedicated_policy_for("a-shares", "valuation")
    hk = dedicated_policy_for("hk", "valuation")
    assert a is not None and hk is not None
    assert a.classify("000001", [ValuationData(symbol="000001", date=NOW.date(), pe_ttm=10)], None, None, NOW) == "complete"
    assert a.classify("000001", [ValuationData(symbol="000001", date=NOW.date())], None, None, NOW) == "failed"
    assert a.classify("000001", [ValuationData(symbol="000001", date=NOW.date(),
                                              pe_ttm=float("nan"), ps_ttm=10)], None, None, NOW) == "failed"
    assert hk.classify("00700.HK", [ValuationData(symbol="00700.HK", date=NOW.date(), pe_ttm=-1, pb=-2)], None, None, NOW) == "partial"
    assert hk.classify("00700.HK", [ValuationData(symbol="00700.HK", date=NOW.date(), pe_ttm=10, cache_degraded=True)], None, None, NOW) == "partial"
    assert hk.classify("00700.HK", [ValuationData(symbol="00700.HK", date=NOW.date(), pe_ttm=10)], None, None, NOW) == "complete"


def test_industry_policy_states():
    for market in ("a-shares", "hk"):
        policy = dedicated_policy_for(market, "industry")
        assert policy is not None
        complete = IndustryData(symbol="000001", industry="银行", sector="金融", comparison_ready=True)
        partial = complete.model_copy(update={"comparison_snapshot": {"errors": ["同行报价失败"]}})
        failed = IndustryData(symbol="000001", industry="未知", sector="")
        assert policy.classify("000001", [complete], None, None, NOW) == "complete"
        assert policy.classify("000001", [partial], None, None, NOW) == "partial"
        assert policy.classify("000001", [failed], None, None, NOW) == "failed"

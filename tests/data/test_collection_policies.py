"""五组能力的有效性、状态和参考窗口判断。"""
from datetime import date, datetime, timedelta
from typing import Literal

from data.collection_policies import POLICIES, policy_for
from data.hk_news import HK_TZ
from data.schemas import (
    DataCollectionRecord,
    FinancialData,
    NewsData,
    PriceData,
    RawSentimentData,
    RawSentimentItem,
)

NOW = datetime(2026, 10, 26, 12, tzinfo=HK_TZ)


def prices(count: int = 150, symbol: str = "000001") -> list[PriceData]:
    return [
        PriceData(
            symbol=symbol,
            trade_date=date(2026, 10, 25) - timedelta(days=index),
            open=10, high=11, low=9, close=10, volume=100,
        )
        for index in range(count)
    ]


def news_item(title: str, publish_date: date | None = None) -> RawSentimentItem:
    return RawSentimentItem(
        title=title, source="news", publish_date=publish_date or NOW.date()
    )


def news(rows: list[RawSentimentItem], symbol: str = "000001") -> list[NewsData]:
    row = NewsData(symbol=symbol, date=NOW.date(), headlines=[item.title for item in rows])
    row._raw_sentiment = RawSentimentData(
        symbol=symbol, fetch_date=NOW.date(), items=rows
    )
    return [row]


def financial_rows(
    status: Literal["complete", "partial"] = "complete", symbol: str = "000001"
) -> list[FinancialData]:
    return [
        FinancialData(
            symbol=symbol,
            fiscal_quarter=date(2026, 9, 30) - timedelta(days=90 * index),
            revenue=100,
            net_profit=10,
            total_assets=100,
            total_equity=50,
            operating_cash_flow=12,
            basic_eps=1,
            report_currency="CNY",
            financial_selection_status=status,
        )
        for index in range(4)
    ]


def test_policy_registry_covers_migrated_capabilities():
    expected = {
        ("a-shares", "financial"),
        ("hk", "financial"),
        ("a-shares", "price"),
        ("hk", "price"),
        ("a-shares", "news"),
    }
    assert set(POLICIES) == expected
    assert all(policy.source_attempts == 2 for policy in POLICIES.values())


def test_financial_policy_complete_partial_failed_and_not_applicable():
    policy = policy_for("a-shares", "financial")
    assert policy is not None
    assert policy.classify("000001", financial_rows("complete"), None, None, NOW) == "complete"
    assert policy.classify("000001", financial_rows("partial"), None, None, NOW) == "partial"
    assert policy.classify("000001", [], None, None, NOW) == "failed"
    diagnostic = DataCollectionRecord(cache_state="not_applicable")
    assert policy.classify("000001", [], diagnostic, None, NOW) == "not_applicable"


def test_price_policy_coverage_and_invalid_rows():
    policy = policy_for("a-shares", "price")
    assert policy is not None
    full = prices(150)
    short = prices(20)
    assert policy.classify("000001", full, None, None, NOW) == "complete"
    assert policy.classify("000001", short, None, full, NOW) == "partial"
    assert policy.fetch_constraints(full, NOW)["minimum_points"] == 150
    invalid = prices(1)
    invalid[0].close = -1
    assert policy.classify("000001", invalid, None, None, NOW) == "failed"
    diagnostic = DataCollectionRecord(cache_state="not_applicable")
    assert policy.classify("000001", short, diagnostic, full, NOW) == "not_applicable"


def test_news_policy_empty_error_partial_and_complete():
    policy = policy_for("a-shares", "news")
    assert policy is not None
    empty = news([])
    assert policy.classify("000001", empty, None, None, NOW) == "empty"
    error = DataCollectionRecord(error_kind="timeout", error_message="新闻接口失败")
    assert policy.classify("000001", empty, error, None, NOW) == "failed"
    one = news([news_item("消息")])
    assert policy.classify("000001", one, error, None, NOW) == "partial"
    assert policy.classify("000001", one, None, None, NOW) == "complete"


def test_news_policy_requires_raw_sentiment_and_rejects_future_items():
    policy = policy_for("a-shares", "news")
    assert policy is not None
    without_raw = [NewsData(symbol="000001", date=NOW.date(), headlines=["标题"])]
    assert policy.classify("000001", without_raw, None, None, NOW) == "failed"
    future = news([news_item("未来", NOW.date() + timedelta(days=1))])
    assert policy.classify("000001", future, None, None, NOW) == "failed"


def test_news_reference_filters_30_day_window_and_rebuilds_headlines():
    policy = policy_for("a-shares", "news")
    assert policy is not None
    old = news_item("旧消息", NOW.date() - timedelta(days=40))
    recent = news_item("新消息", NOW.date() - timedelta(days=1))
    reference = policy.reference_rows(news([old, recent]), NOW)
    assert reference is not None
    assert reference[0].headlines == ["新消息"]

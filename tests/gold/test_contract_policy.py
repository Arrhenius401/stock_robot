"""黄金专用结果策略：当前价格有效性与历史覆盖分开判断。"""

from datetime import UTC, date, datetime, timedelta

import pytest

from gold.models import GoldBar, GoldObservation, GoldRules
from gold.policy import classify_gold
from gold.sources import empty_series

NOW = datetime(2026, 10, 26, 10, tzinfo=UTC)
TODAY = NOW.date()
RULES = GoldRules()


def daily(key, end=TODAY, count=2200):
    result = empty_series(key, NOW, "")
    result.status, result.error = "ok", None
    dates = [end - timedelta(days=count - i - 1) for i in range(count)]
    if key in ("domestic", "international"):
        result.bars = [GoldBar(trade_date=d, open=100, high=101, low=99, close=100) for d in dates]
    else:
        result.observations = [GoldObservation(observation_date=d, value=1) for d in dates]
    return result


def reserves(end_month=9):
    result = empty_series("china_reserves", NOW, "")
    result.status, result.error = "ok", None
    dates = []
    for year, first, last in ((2025, 1, 12), (2026, 1, end_month)):
        for month in range(first, last + 1):
            after = date(year + (month == 12), month % 12 + 1, 1)
            dates.append(after - timedelta(days=1))
    result.observations = [GoldObservation(observation_date=d, value=100) for d in dates]
    return result


@pytest.mark.parametrize("key", ["domestic", "international", "real_yield", "dollar", "fx", "holdings"])
def test_full_history_is_complete(key):
    assert classify_gold(daily(key), None, NOW, RULES) == "complete"


def test_incomplete_history_keeps_usable_price_but_is_partial():
    short = daily("domestic", count=80)
    assert short.bars[-1].close == 100
    assert classify_gold(short, None, NOW, RULES) == "partial"


def test_completed_last_observation_day_defines_five_year_coverage():
    # 周日生成报告时，五年跨度基于上周五最新完成日，而非周日。
    end = date(2026, 10, 23)
    item = daily("international", end=end, count=1827)
    assert classify_gold(item, None, NOW, RULES) == "complete"


def test_monthly_history_requires_contiguous_month_end_data():
    complete = reserves()
    assert classify_gold(complete, None, NOW, RULES) == "complete"
    complete.observations.pop(5)
    assert classify_gold(complete, None, NOW, RULES) == "partial"


def test_coverage_regression_and_old_market_date_are_partial():
    previous = daily("domestic")
    fewer = daily("domestic")
    fewer.bars.pop(-20)
    assert classify_gold(fewer, previous, NOW, RULES) == "partial"
    earlier = daily("domestic", end=NOW.date() - timedelta(days=1))
    assert classify_gold(earlier, previous, NOW, RULES) == "partial"


def test_rolling_window_does_not_count_expired_points_as_regression():
    previous = daily("domestic", end=NOW.date() - timedelta(days=1), count=2200)
    # 新源从本次五年加30日的请求起点开始，正常丢弃此前的数据。
    cutoff = date(2021, 10, 26) - timedelta(days=30)
    current = daily("domestic", count=(NOW.date() - cutoff).days + 1)
    assert classify_gold(current, previous, NOW, RULES) == "complete"


def test_stale_data_is_partial_and_future_dates_are_failed():
    stale = daily("domestic", end=NOW.date() - timedelta(days=11))
    assert classify_gold(stale, None, NOW, RULES) == "partial"
    future = daily("domestic", end=NOW.date() + timedelta(days=1))
    assert classify_gold(future, None, NOW, RULES) == "failed"


def test_different_source_freshness_limits_are_respected():
    for key in ("dollar", "fx"):
        assert classify_gold(daily(key, end=TODAY - timedelta(days=14)), None, NOW, RULES) == "complete"
        assert classify_gold(daily(key, end=TODAY - timedelta(days=15)), None, NOW, RULES) == "partial"
    assert classify_gold(reserves(end_month=7), None, NOW, RULES) == "complete"
    assert classify_gold(reserves(end_month=6), None, NOW, RULES) == "partial"


def test_negative_real_yield_is_valid_and_future_publication_is_rejected():
    item = daily("real_yield")
    item.observations[-1].value = -0.5
    assert classify_gold(item, None, NOW, RULES) == "complete"
    item.published_at = NOW + timedelta(hours=1)
    assert classify_gold(item, None, NOW, RULES) == "failed"
    # 来源只提供日期时，同一来源本地日期内的时间不代表未来发布。
    item.metadata["published_precision"] = "date"
    assert classify_gold(item, None, NOW, RULES) == "complete"


def test_five_year_rank_requires_prior_warmup():
    item = daily("real_yield", count=1827)
    assert classify_gold(item, None, NOW, RULES) == "partial"
    assert classify_gold(daily("real_yield", count=2200), None, NOW, RULES) == "complete"


@pytest.mark.parametrize("problem", ["wrong_unit", "wrong_kind", "duplicate", "nan", "bad_price", "negative"])
def test_invalid_source_data_fails(problem):
    item = daily("domestic" if problem != "negative" else "fx")
    if problem == "wrong_unit":
        item.unit = "美元/盎司"
    elif problem == "wrong_kind":
        item.observations = [GoldObservation(observation_date=NOW.date(), value=1)]
    elif problem == "duplicate":
        item.bars.append(item.bars[-1])
    elif problem == "nan":
        item.bars[-1].close = float("nan")
    elif problem == "bad_price":
        item.bars[-1].high = 98
    else:
        item.observations[-1].value = -1
    assert classify_gold(item, None, NOW, RULES) == "failed"


def test_empty_global_or_failed_series_never_produces_empty_state():
    for key in ("domestic", "global_reserves"):
        assert classify_gold(empty_series(key, NOW, "来源不可用"), None, NOW, RULES) == "failed"

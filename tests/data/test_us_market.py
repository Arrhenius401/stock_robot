"""美股交易时段判定：常规时段、盘前/盘后、周末与 NYSE 休市日。"""
from datetime import date, datetime

from data.us_stock import (
    US_TZ,
    us_market_early_closes,
    us_market_holidays,
    us_market_session,
)


def at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=US_TZ)


def test_regular_session_and_pre_close():
    assert us_market_session(at(2026, 11, 13, 9, 0)) == "pre"
    assert us_market_session(at(2026, 11, 13, 9, 30)) == "open"
    assert us_market_session(at(2026, 11, 13, 15, 59)) == "open"
    assert us_market_session(at(2026, 11, 13, 16, 0)) == "closed"


def test_weekend_and_holidays_are_closed():
    assert us_market_session(at(2026, 11, 14, 12, 0)) == "closed"  # 周六
    assert us_market_session(at(2026, 11, 26, 12, 0)) == "closed"  # 感恩节
    assert us_market_session(at(2026, 12, 25, 12, 0)) == "closed"  # 圣诞
    assert us_market_session(at(2026, 4, 3, 12, 0)) == "closed"    # Good Friday


def test_early_close_day_closes_after_one_pm():
    assert us_market_session(at(2026, 11, 27, 12, 0)) == "open"
    assert us_market_session(at(2026, 11, 27, 13, 0)) == "closed"


def test_holiday_and_early_close_sets():
    assert date(2026, 11, 26) in us_market_holidays(2026)
    assert date(2026, 11, 27) in us_market_early_closes(2026)
    assert date(2026, 7, 3) in us_market_early_closes(2026)

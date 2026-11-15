"""A 股/港股交易时段判定。"""
from datetime import datetime

from data.market_hours import HONG_KONG, SHANGHAI, market_session


def at(zone, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=zone)


def test_a_share_session_boundaries_and_weekend():
    assert market_session("a-shares", at(SHANGHAI, 13, 9, 0)) == "pre"
    assert market_session("a-shares", at(SHANGHAI, 13, 9, 30)) == "open"
    assert market_session("a-shares", at(SHANGHAI, 13, 14, 59)) == "open"
    assert market_session("a-shares", at(SHANGHAI, 13, 15, 0)) == "closed"
    assert market_session("a-shares", at(SHANGHAI, 14, 10, 0)) == "closed"


def test_hk_session_boundaries_and_weekend():
    assert market_session("hk", at(HONG_KONG, 13, 9, 0)) == "pre"
    assert market_session("hk", at(HONG_KONG, 13, 9, 30)) == "open"
    assert market_session("hk", at(HONG_KONG, 13, 15, 59)) == "open"
    assert market_session("hk", at(HONG_KONG, 13, 16, 0)) == "closed"
    assert market_session("hk", at(HONG_KONG, 14, 10, 0)) == "closed"

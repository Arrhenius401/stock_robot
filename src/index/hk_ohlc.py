"""以恒生官方交易日和收盘为唯一价格基准，补充腾讯开高低。"""
import logging
from datetime import date
from typing import Any

import requests

from data.hk_alternatives import tencent_history
from data.hk_eastmoney import finite_number
from data.schemas import IndexClosePriceData, IndexPriceData

logger = logging.getLogger(__name__)


def match_hstech_ohlc(official: list[IndexClosePriceData], quotes: list[list[Any]]) -> list[IndexPriceData] | None:
    expected = {row.trade_date for row in official}
    by_day = {}
    for quote in quotes:
        if not isinstance(quote, list) or len(quote) < 5:
            return None
        try:
            day = date.fromisoformat(quote[0])
        except (ValueError, TypeError):
            return None
        if day not in expected:
            continue
        if day in by_day:
            return None
        by_day[day] = quote
    result = []
    for row in official:
        quote = by_day.get(row.trade_date)
        if quote is None:
            return None
        opening, close, high, low = (finite_number(value) for value in quote[1:5])
        if opening is None or close is None or high is None or low is None:
            return None
        if abs(close - row.close) > 0.005 or not 0 < low <= min(opening, row.close, close) <= max(opening, row.close, close) <= high:
            return None
        item = IndexPriceData(**row.model_dump(), open=opening, high=high, low=low,
                              volume=0)
        item._data_source = "恒生官方收盘 + 腾讯 OHLC"
        result.append(item)
    return result


def fetch_hstech_ohlc(official: list[IndexClosePriceData]) -> list[IndexPriceData] | None:
    if not official:
        return None
    first, last = min(row.trade_date for row in official), max(row.trade_date for row in official)
    try:
        quotes = [quote for year in range(first.year, last.year + 1) for quote in tencent_history(
            "HSTECH", str(max(first, date(year, 1, 1))), str(min(last, date(year, 12, 31))), 400)]
        return match_hstech_ohlc(official, quotes)
    except (requests.RequestException, TypeError, ValueError) as error:
        logger.warning("恒生科技腾讯 OHLC 请求失败，保留官方收盘: %s", error)
        return None

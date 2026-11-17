"""三个股票市场共用的当前价格编排，行情时点与采集时点分离。"""
import logging
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from utils.numbers import is_positive_finite

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _us_close_time(value: str, now: datetime) -> datetime:
    """新浪无年份的常规收盘时间；以美东时区处理跨年和夏令时。"""
    from data.us_stock import US_TZ

    match = re.fullmatch(r"([A-Z][a-z]{2}) (\d{1,2}) (\d{2}):(\d{2})(AM|PM) (EST|EDT)", value.strip())
    if not match:
        raise ValueError("缺少有效美股常规收盘时间")
    if not 1 <= int(match[3]) <= 12:
        raise ValueError("美股收盘小时无效")
    month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec").index(match[1]) + 1
    hour = int(match[3]) % 12 + (12 if match[5] == "PM" else 0)
    stamp = datetime(now.year, month, int(match[2]), hour, int(match[4]), tzinfo=US_TZ)
    if stamp > now + timedelta(days=1):
        stamp = stamp.replace(year=now.year - 1)
    if stamp.tzname() != match[6]:
        raise ValueError("美股收盘时区与日期不一致")
    return stamp


def collect_current_quote(market: str, symbol: str, *, daily_end: str | None = None,
                          now: datetime | None = None) -> dict[str, Any]:
    """每次新分析均查询快照；失败只影响当前价格，不改日线及评分。"""
    from data.latest_quote import load_hk_quote, load_latest_quote
    from data.market_hours import market_session
    from data.us_stock import (
        US_TZ,
        us_market_early_closes,
        us_market_holidays,
        us_market_session,
        us_quote,
    )

    zone = US_TZ if market == "us" else SHANGHAI
    current = (now or datetime.now(zone)).astimezone(zone)
    session = us_market_session(current) if market == "us" else market_session(market, current)
    try:
        if market == "a-shares":
            quote = load_latest_quote(symbol, daily_end=daily_end, now=current)
        elif market == "hk":
            quote = load_hk_quote(symbol, now=current, daily_end=daily_end)
        elif market == "us":
            raw = us_quote(symbol)
            # 主价格在闭市期间对应常规收盘，不能采用盘外时间或采集时间。
            stamp = (datetime.fromisoformat(str(raw.get("quoted_at"))).replace(tzinfo=SHANGHAI).astimezone(US_TZ)
                     if session == "open" else _us_close_time(str(raw.get("close_time") or ""), current))
            quote = {"status": "ok", "symbol": symbol, "price": raw.get("price"),
                     "quoted_at": stamp.isoformat(), "source": "新浪美股快照（延迟约15分钟）",
                     "change_pct": raw.get("change_pct")}
        else:
            raise ValueError("不支持的股票市场")
        if quote.get("status") != "ok":
            return {**quote, "note": "报价暂不可用，日线收盘价"}
        stamp = datetime.fromisoformat(str(quote.get("quoted_at")))
        if stamp.tzinfo is None or not is_positive_finite(quote.get("price")):
            raise ValueError("报价或行情时点无效")
        stamp = stamp.astimezone(zone)
        if (stamp > current + timedelta(minutes=2) or stamp < current - timedelta(days=14)
                or (daily_end and stamp.date().isoformat() < daily_end)
                or (market == "us" and session == "open" and stamp.date() != current.date())):
            raise ValueError("行情时点过旧、早于日线或位于未来")
        holiday = (market == "us" and current.date() in us_market_holidays(current.year))
        if current.weekday() >= 5 or holiday:
            state = "休市，最近收盘价"
        elif session == "pre":
            state = "未开盘，最近收盘价"
        elif session == "open":
            state = "交易中" if stamp.date() == current.date() else "休市或行情未更新，最近报价"
        elif stamp.date() == current.date():
            close_hour = 15 if market == "a-shares" else 16
            if market == "us" and current.date() in us_market_early_closes(current.year):
                close_hour = 13
            state = "已收盘" if stamp.hour >= close_hour else "已收盘，行情未更新"
        else:
            state = "休市或行情未更新，最近报价"
        return {**quote, "market_state": state, "quoted_at": stamp.isoformat(), "note": ""}
    except Exception as exc:  # noqa: BLE001 — 第三方报价边界，故障不影响日线报告
        logger.warning("当前报价不可用 %s/%s: %s", market, symbol, type(exc).__name__)
        return {"status": "unavailable", "symbol": symbol,
                "note": "报价暂不可用，日线收盘价", "reason": str(exc)[:160]}

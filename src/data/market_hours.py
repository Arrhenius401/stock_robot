"""A 股与港股的常规交易时段判定；只判断工作日与时间窗口，节假日由报价时点兜底。"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
_SESSIONS = {
    "a-shares": (SHANGHAI, time(9, 30), time(15, 0)),
    "hk": (HONG_KONG, time(9, 30), time(16, 0)),
}


def market_session(market: str, now: datetime | None = None) -> str:
    """返回 pre/open/closed；午间休市按当日交易时段处理，不返回额外状态。"""
    try:
        zone, start, end = _SESSIONS[market]
    except KeyError as error:
        raise ValueError(f"不支持的市场: {market}") from error
    current = now or datetime.now(zone)
    current = current.replace(tzinfo=zone) if current.tzinfo is None else current.astimezone(zone)
    if current.weekday() >= 5:
        return "closed"
    if current.time() < start:
        return "pre"
    return "open" if current.time() < end else "closed"

"""从已缓存上市交易日历准备股票研究窗口，不联网或修改日历。"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from typing import Any

from radar.calendar import CalendarData, CalendarUnavailable, TradingCalendar
from radar.stocks.admission import _BJ, _day

MARKET_WINDOW_VERSION = 'stock-market-window-v1'


def _validate_snapshot(data: CalendarData | None, now: datetime) -> CalendarData:
    if not isinstance(data, CalendarData):
        raise CalendarUnavailable('缺少有效缓存日历，不能补推工作日')
    if (not isinstance(data.days, tuple) or not data.days
            or any(type(day) is not date for day in data.days)
            or type(data.coverage_start) is not date or type(data.coverage_end) is not date
            or data.coverage_start > data.coverage_end or tuple(sorted(set(data.days))) != data.days
            or data.days[0] < data.coverage_start or data.days[-1] > data.coverage_end):
        raise CalendarUnavailable('日历日期或覆盖元数据无效')
    if (not isinstance(data.fetched_at, datetime) or data.fetched_at.tzinfo is None
            or data.fetched_at.utcoffset() is None or data.fetched_at > now
            or not isinstance(data.source, str) or not data.source.strip()):
        raise CalendarUnavailable('日历来源或抓取时间无效/晚于当前时间')
    return data


def prepare_market_window(calendar: TradingCalendar, as_of: date, *, now: datetime) -> dict[str, Any]:
    """取截至有效时点已收盘的60个明确交易日，窗口只是研究上下文。"""
    cutoff = _day(as_of)
    if (not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None):
        raise ValueError('当前时间必须包含有效时区')
    local = now.astimezone(_BJ)
    if cutoff is None or cutoff > local.date():
        raise ValueError('研究日期无效或晚于北京当前日期')
    if calendar.market != 'XSHG':
        raise ValueError('股票研究窗口目前只支持XSHG市场')
    effective = min(datetime.combine(cutoff, time.max, tzinfo=_BJ), local)
    # 只使用公开只读入口，避免latest_completed/trading_days间接刷新缓存。
    data = _validate_snapshot(calendar.cached_data(), local)
    if not data.coverage_start <= effective.date() <= data.coverage_end:
        raise CalendarUnavailable('日历未覆盖有效时点日期，不能挪动研究截止日')
    eligible = [day for day in data.days if day < effective.date()
                or (day == effective.date() and effective.time() >= time(15))]
    if len(eligible) < 60:
        raise CalendarUnavailable('已收盘的明确市场交易日不足60个，不能补工作日或较早行情')
    snapshot = {'days': [day.isoformat() for day in data.days], 'coverage_start': data.coverage_start.isoformat(),
                'coverage_end': data.coverage_end.isoformat(), 'fetched_at': data.fetched_at.isoformat(), 'source': data.source}
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return {'version': MARKET_WINDOW_VERSION, 'market': 'XSHG', 'as_of': cutoff.isoformat(),
            'effective_at': effective.isoformat(), 'days': [day.isoformat() for day in eligible[-60:]],
            'latest_completed': eligible[-1].isoformat(),
            'calendar': {key: value for key, value in snapshot.items() if key != 'days'}
                        | {'snapshot_sha256': hashlib.sha256(canonical).hexdigest()},
            'research_only': True, 'formal_screening_enabled': False}

"""上市市场交易日历；无有效覆盖时拒绝猜测工作日。"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class CalendarUnavailable(RuntimeError):
    """日历不可用或所需日期不在已验证覆盖范围。"""


@dataclass(frozen=True)
class CalendarData:
    days: tuple[date, ...]
    coverage_start: date
    coverage_end: date
    fetched_at: datetime
    source: str


def fetch_akshare_calendar(*, timeout: float = 20.0) -> CalendarData:
    """第三方调用在子进程中执行，超时后由 subprocess 终止回收。"""
    code = (
        "import akshare as ak,json; df=ak.tool_trade_date_hist_sina(); "
        "print(json.dumps([str(day) for day in df['trade_date']]))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=True,
        )
        raw = json.loads(result.stdout)
        if not isinstance(raw, list) or not raw:
            raise ValueError("交易日期列表为空或格式异常")
        days = tuple(sorted({date.fromisoformat(day) for day in raw}))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        logger.warning("上市交易日历下载失败: %s", exc)
        raise CalendarUnavailable("交易日历下载失败，请检查网络后重试") from exc
    # 源仅返回交易日，末日之后的假期/年份不能据此推断覆盖。
    return CalendarData(
        days, days[0], days[-1], datetime.now().astimezone(), "akshare:sina:XSHG"
    )


class TradingCalendar:
    """按上市日历计算目标日，境内上市海外 ETF 仍使用境内交易日。"""

    def __init__(
        self,
        db_path: str | Path,
        fetcher: Callable[[], CalendarData] | None = None,
        close_time: time = time(15),
        timezone: str = "Asia/Shanghai",
        market: str = "XSHG",
        refresh_enabled: bool = True,
    ) -> None:
        if market != "XSHG" and fetcher is None:
            raise ValueError("非境内上市市场必须提供对应交易日历数据源")
        self.db_path = Path(db_path)
        self.fetcher = fetcher or fetch_akshare_calendar
        self.close_time = close_time
        self.timezone = ZoneInfo(timezone)
        self.market = market
        self.refresh_enabled = refresh_enabled
        self._retry_after: datetime | None = None
        if close_time.tzinfo is not None:
            raise ValueError("闭市时间使用上市市场本地时间")
        if not refresh_enabled:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS radar_calendar_cache (
                market TEXT PRIMARY KEY, days TEXT NOT NULL,
                coverage_start TEXT NOT NULL, coverage_end TEXT NOT NULL,
                fetched_at TEXT NOT NULL, source TEXT NOT NULL)""")

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            return sqlite3.connect(
                self.db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10
            )
        return sqlite3.connect(self.db_path, timeout=10)

    def _validate(self, data: CalendarData) -> CalendarData:
        if (
            not data.days
            or data.coverage_start > data.coverage_end
            or data.fetched_at.tzinfo is None
            or tuple(sorted(set(data.days))) != data.days
            or data.days[0] < data.coverage_start
            or data.days[-1] > data.coverage_end
        ):
            raise CalendarUnavailable("交易日历覆盖范围或日期格式异常")
        return data

    def _read(self) -> CalendarData | None:
        if not self.db_path.exists():
            return None
        try:
            with closing(self._connect(readonly=True)) as conn:
                row = conn.execute(
                    "SELECT days,coverage_start,coverage_end,fetched_at,source FROM radar_calendar_cache WHERE market=?",
                    (self.market,),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            logger.warning("交易日历缓存无法读取: %s", exc)
            return None
        if row is None:
            return None
        try:
            return self._validate(
                CalendarData(
                    tuple(date.fromisoformat(day) for day in json.loads(row[0])),
                    date.fromisoformat(row[1]),
                    date.fromisoformat(row[2]),
                    datetime.fromisoformat(row[3]),
                    row[4],
                )
            )
        except (ValueError, TypeError, CalendarUnavailable) as exc:
            logger.warning("交易日历缓存无效: %s", exc)
            return None

    def cached_data(self) -> CalendarData | None:
        """读取缓存元数据，不触发网络请求。"""
        return self._read()

    def refresh(self) -> CalendarData:
        """由采集服务显式刷新，配置页只读实例不得调用。"""
        return self._refresh()

    def _refresh(self) -> CalendarData:
        if not self.refresh_enabled:
            raise CalendarUnavailable("交易日历覆盖不足，请等待采集服务刷新日历")
        now = datetime.now().astimezone()
        if self._retry_after is not None and now < self._retry_after:
            raise CalendarUnavailable("交易日历刷新失败后等待重试")
        try:
            data = self._validate(self.fetcher())
        except (
            CalendarUnavailable,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
        ) as exc:
            logger.warning("交易日历刷新失败: %s", exc)
            self._retry_after = now + timedelta(minutes=5)
            raise CalendarUnavailable("交易日历刷新失败，覆盖不足时停止采集") from exc
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO radar_calendar_cache VALUES(?,?,?,?,?,?)
                ON CONFLICT(market) DO UPDATE SET days=excluded.days,
                coverage_start=excluded.coverage_start,coverage_end=excluded.coverage_end,
                fetched_at=excluded.fetched_at,source=excluded.source""",
                (
                    self.market,
                    json.dumps([day.isoformat() for day in data.days]),
                    data.coverage_start.isoformat(),
                    data.coverage_end.isoformat(),
                    data.fetched_at.isoformat(),
                    data.source,
                ),
            )
        # 下载成功但年份覆盖仍不足时，同样限频，避免每次轮询重下。
        self._retry_after = now + timedelta(minutes=5)
        return data

    def _covered(self, start: date, end: date) -> CalendarData:
        data = self._read()
        valid = (
            data is not None
            and data.coverage_start <= start <= end <= data.coverage_end
        )
        now = datetime.now().astimezone()
        stale = data is not None and now - data.fetched_at > timedelta(days=1)
        if not valid or (stale and self.refresh_enabled):
            try:
                data = self._refresh()
            except CalendarUnavailable:
                if not valid:
                    raise CalendarUnavailable(
                        f"交易日历覆盖不足: {start} 至 {end}"
                    ) from None
                logger.info("日历刷新不可用，沿用已验证覆盖的缓存")
        if data is None or not data.coverage_start <= start <= end <= data.coverage_end:
            raise CalendarUnavailable(f"交易日历覆盖不足: {start} 至 {end}")
        return data

    def _local(self, now: datetime) -> datetime:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("当前时间必须包含时区")
        return now.astimezone(self.timezone)

    def latest_completed(self, now: datetime) -> date:
        local = self._local(now)
        data = self._covered(local.date(), local.date())
        eligible = tuple(
            day
            for day in data.days
            if day < local.date()
            or (day == local.date() and local.time() >= self.close_time)
        )
        if not eligible:
            raise CalendarUnavailable("交易日历中没有已闭市交易日")
        return eligible[-1]

    def trading_days(self, start: date, end: date) -> tuple[date, ...]:
        if start > end:
            raise ValueError("开始日期不得晚于结束日期")
        data = self._covered(start, end)
        return tuple(day for day in data.days if start <= day <= end)

    def next_scheduled(self, now: datetime, hour: int, minute: int) -> datetime:
        local = self._local(now)
        schedule_time = time(hour, minute)
        data = self._covered(local.date(), local.date())
        for day in data.days:
            if day < local.date():
                continue
            scheduled = datetime.combine(day, schedule_time, self.timezone)
            if scheduled > local:
                return scheduled
        raise CalendarUnavailable("交易日历覆盖不足，无法确定下次采集时间")

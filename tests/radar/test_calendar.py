"""交易日历边界与持久缓存测试。"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from radar.calendar import CalendarData, CalendarUnavailable, TradingCalendar

TZ = ZoneInfo("Asia/Shanghai")
DAYS = (
    date(2025, 12, 31),
    date(2026, 1, 5),
    date(2026, 1, 6),
    date(2026, 1, 9),
    date(2026, 1, 12),
)


def data():
    return CalendarData(
        DAYS,
        date(2025, 12, 30),
        date(2026, 1, 15),
        datetime(2026, 1, 1, tzinfo=TZ),
        "测试上市日历",
    )


def calendar(tmp_path):
    return TradingCalendar(tmp_path / "calendar.db", fetcher=data)


@pytest.mark.parametrize(
    "instant,expected",
    [
        (datetime(2026, 1, 5, 14, 59, tzinfo=TZ), date(2025, 12, 31)),
        (datetime(2026, 1, 5, 15, 0, tzinfo=TZ), date(2026, 1, 5)),
        (datetime(2026, 1, 3, 18, 30, tzinfo=TZ), date(2025, 12, 31)),
        (datetime(2026, 1, 10, 18, 30, tzinfo=TZ), date(2026, 1, 9)),
    ],
)
def test_latest_completed(tmp_path, instant, expected):
    assert calendar(tmp_path).latest_completed(instant) == expected


def test_timezone_is_beijing(tmp_path):
    assert calendar(tmp_path).latest_completed(
        datetime(2026, 1, 5, 7, tzinfo=ZoneInfo("UTC"))
    ) == date(2026, 1, 5)


def test_next_schedule_skips_holidays_and_weekends(tmp_path):
    cal = calendar(tmp_path)
    assert cal.next_scheduled(
        datetime(2025, 12, 31, 19, tzinfo=TZ), 18, 30
    ) == datetime(2026, 1, 5, 18, 30, tzinfo=TZ)
    assert cal.next_scheduled(
        datetime(2026, 1, 9, 18, 30, tzinfo=TZ), 18, 30
    ) == datetime(2026, 1, 12, 18, 30, tzinfo=TZ)


def test_trading_days_lists_only_explicit_days(tmp_path):
    assert (
        calendar(tmp_path).trading_days(date(2026, 1, 1), date(2026, 1, 9)) == DAYS[1:4]
    )


def test_cache_survives_restart_and_network_failure(tmp_path):
    db = tmp_path / "calendar.db"
    TradingCalendar(db, fetcher=data).latest_completed(
        datetime(2026, 1, 5, 18, tzinfo=TZ)
    )

    def fail():
        raise OSError("网络中断")

    cal = TradingCalendar(db, fetcher=fail)
    assert cal.latest_completed(datetime(2026, 1, 10, 18, tzinfo=TZ)) == date(
        2026, 1, 9
    )
    with pytest.raises(CalendarUnavailable, match="覆盖"):
        cal.latest_completed(datetime(2026, 1, 16, 18, tzinfo=TZ))


def test_insufficient_future_never_assumes_weekdays(tmp_path):
    limited = CalendarData(
        (date(2026, 1, 9),),
        date(2026, 1, 9),
        date(2026, 1, 9),
        datetime(2026, 1, 9, tzinfo=TZ),
        "限定",
    )
    cal = TradingCalendar(tmp_path / "calendar.db", fetcher=lambda: limited)
    with pytest.raises(CalendarUnavailable):
        cal.next_scheduled(datetime(2026, 1, 9, 19, tzinfo=TZ), 18, 30)
    with pytest.raises(CalendarUnavailable):
        cal.trading_days(date(2026, 1, 9), date(2026, 1, 12))


def test_naive_and_invalid_schedule_rejected(tmp_path):
    cal = calendar(tmp_path)
    with pytest.raises(ValueError):
        cal.latest_completed(datetime(2026, 1, 5, 18, tzinfo=None))  # noqa: DTZ001 — 验证拒绝无时区输入
    with pytest.raises(ValueError):
        cal.next_scheduled(datetime(2026, 1, 5, 18, tzinfo=TZ), 25, 30)


def test_custom_listing_close_supported(tmp_path):
    cal = TradingCalendar(tmp_path / "calendar.db", fetcher=data, close_time=time(16))
    assert cal.latest_completed(datetime(2026, 1, 5, 15, 30, tzinfo=TZ)) == date(
        2025, 12, 31
    )


def test_invalid_calendar_coverage_rejected(tmp_path):
    invalid = CalendarData(
        DAYS,
        date(2026, 1, 1),
        date(2026, 1, 15),
        datetime(2026, 1, 1, tzinfo=TZ),
        "异常",
    )
    with pytest.raises(CalendarUnavailable):
        TradingCalendar(tmp_path / "calendar.db", fetcher=lambda: invalid).trading_days(
            date(2026, 1, 5), date(2026, 1, 6)
        )


def test_read_only_status_never_fetches(tmp_path):
    db = tmp_path / "calendar.db"
    TradingCalendar(db, fetcher=data).latest_completed(
        datetime(2026, 1, 5, 18, tzinfo=TZ)
    )

    def forbidden():
        pytest.fail("只读配置查询不得请求网络")

    cal = TradingCalendar(db, fetcher=forbidden, refresh_enabled=False)
    cached = cal.cached_data()
    assert cached is not None
    assert cached.source == "测试上市日历"
    assert cal.latest_completed(datetime(2026, 1, 5, 18, tzinfo=TZ)) == date(2026, 1, 5)
    with pytest.raises(CalendarUnavailable):
        cal.next_scheduled(datetime(2026, 1, 16, 18, tzinfo=TZ), 18, 30)
    with pytest.raises(CalendarUnavailable):
        cal.refresh()


def test_default_download_hard_timeout(monkeypatch):
    import subprocess

    from radar.calendar import fetch_akshare_calendar

    def timed_out(*args, **kwargs):
        assert kwargs["timeout"] == 0.2
        raise subprocess.TimeoutExpired(args[0], 0.2)

    monkeypatch.setattr(subprocess, "run", timed_out)
    with pytest.raises(CalendarUnavailable, match="下载失败"):
        fetch_akshare_calendar(timeout=0.2)


def test_default_download_preserves_actual_coverage(monkeypatch):
    import subprocess

    from radar.calendar import fetch_akshare_calendar

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 0, '["2026-01-09","2026-01-05"]', ""
        ),
    )
    result = fetch_akshare_calendar()
    assert result.coverage_start == date(2026, 1, 5)
    assert result.coverage_end == date(2026, 1, 9)
    assert result.fetched_at.tzinfo is not None


def test_morning_schedule_uses_previous_closed_target(tmp_path):
    cal = calendar(tmp_path)
    instant = datetime(2026, 1, 5, 8, tzinfo=TZ)
    assert cal.next_scheduled(instant, 9, 0) == datetime(2026, 1, 5, 9, tzinfo=TZ)
    assert cal.latest_completed(instant) == date(2025, 12, 31)


def test_read_only_missing_cache_does_not_create_files(tmp_path):
    db = tmp_path / "absent" / "calendar.db"
    cal = TradingCalendar(db, refresh_enabled=False)
    assert cal.cached_data() is None
    with pytest.raises(CalendarUnavailable):
        cal.latest_completed(datetime(2026, 1, 5, 18, tzinfo=TZ))
    assert not db.parent.exists()


def test_foreign_listing_requires_its_own_calendar(tmp_path):
    with pytest.raises(ValueError, match="对应交易日历"):
        TradingCalendar(tmp_path / "calendar.db", market="XNYS")


def test_coverage_exhaustion_does_not_download_twice(tmp_path):
    calls = []

    def source():
        calls.append(1)
        return CalendarData(
            (date(2026, 1, 9),),
            date(2026, 1, 9),
            date(2026, 1, 9),
            datetime.now().astimezone(),
            "实际源",
        )

    cal = TradingCalendar(tmp_path / "calendar.db", fetcher=source)
    with pytest.raises(CalendarUnavailable):
        cal.next_scheduled(datetime(2026, 1, 9, 19, tzinfo=TZ), 18, 30)
    assert len(calls) == 1


def test_insufficient_coverage_is_rate_limited_across_polls(tmp_path):
    calls = []

    def source():
        calls.append(1)
        return CalendarData(
            (date(2026, 1, 9),),
            date(2026, 1, 9),
            date(2026, 1, 9),
            datetime.now().astimezone(),
            "限定源",
        )

    cal = TradingCalendar(tmp_path / "calendar.db", fetcher=source)
    for _ in range(2):
        with pytest.raises(CalendarUnavailable):
            cal.latest_completed(datetime(2026, 1, 12, 18, tzinfo=TZ))
    assert calls == [1]

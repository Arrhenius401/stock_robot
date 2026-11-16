"""统一股票市场日期窗口的只读和收盘边界回归，使用合成日历。"""
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any, cast

import pytest
from click.testing import CliRunner

from radar.calendar import CalendarUnavailable, TradingCalendar
from radar.stocks.market_context import prepare_market_window
from stock_robot.stock_market_context import market_window_command

NOW = datetime.fromisoformat('2026-10-15T16:00:00+08:00')
AS_OF = NOW.date()


def seed(tmp_path, *, count=120, end='2026-12-31'):
    path = tmp_path/'calendar.db'
    # 合成源明确列出交易日，长假留空，业务代码不可自行补工作日。
    days = [date(2026, 5, 1)+timedelta(days=index) for index in range(168)]
    days = [day.isoformat() for day in days if day.weekday() < 5 and not date(2026,10,1) <= day <= date(2026,10,7)]
    days = [day for day in days if day <= end][-count:]
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('CREATE TABLE radar_calendar_cache (market TEXT PRIMARY KEY, days TEXT, coverage_start TEXT, coverage_end TEXT, fetched_at TEXT, source TEXT)')
        connection.execute('INSERT INTO radar_calendar_cache VALUES (?,?,?,?,?,?)',
            ('XSHG',json.dumps(days),'2026-05-01',end,'2026-10-14T10:00:00+08:00','synthetic:explicit:XSHG'))
    return TradingCalendar(path, refresh_enabled=False)


def test_exact_sixty_days_and_canonical_snapshot_without_writes_or_network(tmp_path, monkeypatch):
    calendar = seed(tmp_path)
    before = calendar.db_path.read_bytes()
    monkeypatch.setattr(calendar, 'fetcher', lambda: pytest.fail('不得联网'))
    monkeypatch.setattr(calendar, 'refresh', lambda: pytest.fail('不得刷新'))
    original = calendar.cached_data
    calls = []
    def read_once():
        calls.append(1)
        return original()
    monkeypatch.setattr(calendar, 'cached_data', read_once)
    result = prepare_market_window(calendar, AS_OF, now=NOW)
    assert len(calls) == 1
    assert len(result['days']) == len(set(result['days'])) == 60
    assert result['days'] == sorted(result['days'])
    assert result['days'][-1] == result['latest_completed'] == '2026-10-15'
    assert result['market'] == 'XSHG'
    assert result['version'] == 'stock-market-window-v1'
    assert result['formal_screening_enabled'] is False
    assert result['research_only'] is True
    data = original()
    assert data is not None
    snapshot = {'days':[day.isoformat() for day in data.days], 'coverage_start':data.coverage_start.isoformat(),
                'coverage_end':data.coverage_end.isoformat(),'fetched_at':data.fetched_at.isoformat(),'source':data.source}
    digest = hashlib.sha256(json.dumps(snapshot,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert result['calendar']['snapshot_sha256'] == digest
    assert calendar.db_path.read_bytes() == before
    assert not list(tmp_path.glob('calendar.db-*'))


@pytest.mark.parametrize('instant,latest', [('2026-10-15T14:59:59+08:00','2026-10-14'),
    ('2026-10-15T15:00:00+08:00','2026-10-15'), ('2026-10-15T06:59:00+00:00','2026-10-14'),
    ('2026-10-15T07:00:00+00:00','2026-10-15'), ('2026-10-07T16:00:00+08:00','2026-09-30'),
    ('2026-10-11T16:00:00+08:00','2026-10-09')])
def test_close_time_utc_holiday_weekend(tmp_path, instant, latest):
    calendar = seed(tmp_path)
    # 合成元数据必须早于实验时刻。
    with closing(sqlite3.connect(calendar.db_path)) as connection, connection:
        connection.execute("UPDATE radar_calendar_cache SET fetched_at='2026-09-01T10:00:00+08:00'")
    now = datetime.fromisoformat(instant)
    result = prepare_market_window(calendar, now.astimezone(calendar.timezone).date(), now=now)
    assert result['latest_completed'] == latest


def test_historical_cutoff_is_day_end_even_when_now_intraday(tmp_path):
    result = prepare_market_window(seed(tmp_path),date(2026,10,14),now=datetime.fromisoformat('2026-10-15T09:00:00+08:00'))
    assert result['latest_completed'] == '2026-10-14'
    assert result['effective_at'] == '2026-10-14T23:59:59.999999+08:00'


@pytest.mark.parametrize('change', ['count','coverage','future_asof','naive_now','bad_asof','future_fetch','empty_source','missing','market','duplicate','naive_fetch'])
def test_invalid_context_rejected_without_fallback(tmp_path,monkeypatch,change):
    calendar = seed(tmp_path,count=59 if change=='count' else 120,end='2026-10-14' if change=='coverage' else '2026-12-31')
    now,as_of=NOW,AS_OF
    data=calendar.cached_data()
    assert data is not None
    if change=='future_asof':
        as_of=date(2026,10,16)
    elif change=='naive_now':
        now=NOW.replace(tzinfo=None)
    elif change=='bad_asof':
        as_of='bad'
    elif change=='future_fetch':
        data=replace(data,fetched_at=NOW+timedelta(seconds=1))
    elif change=='empty_source':
        data=replace(data,source='  ')
    elif change=='missing':
        data=None
    elif change=='market':
        calendar.market='XNYS'
    elif change=='duplicate':
        data=replace(data,days=(*data.days,data.days[-1]))
    elif change=='naive_fetch':
        data=replace(data,fetched_at=NOW.replace(tzinfo=None))
    monkeypatch.setattr(calendar,'cached_data',lambda:data)
    with pytest.raises((CalendarUnavailable,ValueError,TypeError)):
        prepare_market_window(calendar,cast(Any,as_of),now=now)


def test_cli_reads_real_sqlite_and_writes_envelope(tmp_path):
    calendar=seed(tmp_path)
    before=calendar.db_path.read_bytes()
    output=tmp_path/'window.json'
    response=CliRunner().invoke(market_window_command,['--calendar-db',str(calendar.db_path),'--as-of','2026-10-14','--output',str(output)])
    assert response.exit_code==0,response.output
    packet=json.loads(output.read_text(encoding='utf-8'))
    assert packet['days'][-1]=='2026-10-14'
    assert packet['research_only'] is True
    assert calendar.db_path.read_bytes()==before


@pytest.mark.parametrize('suffix',['','-wal','-shm','-journal'])
def test_cli_protects_database_family_before_work(tmp_path,suffix):
    calendar=seed(tmp_path)
    before=calendar.db_path.read_bytes()
    response=CliRunner().invoke(market_window_command,['--calendar-db',str(calendar.db_path),'--as-of','2026-10-14','--output',str(calendar.db_path)+suffix])
    assert response.exit_code!=0
    assert calendar.db_path.read_bytes()==before


def test_empty_or_missing_database_does_not_initialize_tables_or_paths(tmp_path):
    missing=tmp_path/'missing'/'calendar.db'
    calendar=TradingCalendar(missing,refresh_enabled=False)
    with pytest.raises(CalendarUnavailable):
        prepare_market_window(calendar,AS_OF,now=NOW)
    assert not missing.parent.exists()
    empty=tmp_path/'empty.db'
    with closing(sqlite3.connect(empty)) as connection:
        assert connection.execute('SELECT name FROM sqlite_master').fetchall()==[]
    calendar=TradingCalendar(empty,refresh_enabled=False)
    with pytest.raises(CalendarUnavailable):
        prepare_market_window(calendar,AS_OF,now=NOW)
    with closing(sqlite3.connect(empty)) as connection:
        assert connection.execute('SELECT name FROM sqlite_master').fetchall()==[]


def test_snapshot_changes_if_unused_future_calendar_metadata_changes(tmp_path,monkeypatch):
    calendar=seed(tmp_path)
    first=prepare_market_window(calendar,AS_OF,now=NOW)
    data=calendar.cached_data()
    assert data is not None
    monkeypatch.setattr(calendar,'cached_data',lambda:replace(data,coverage_end=date(2027,1,1)))
    second=prepare_market_window(calendar,AS_OF,now=NOW)
    assert first['days']==second['days']
    assert first['calendar']['snapshot_sha256']!=second['calendar']['snapshot_sha256']


def test_midnight_in_utc_uses_beijing_current_day(tmp_path):
    result=prepare_market_window(seed(tmp_path),AS_OF,now=datetime.fromisoformat('2026-10-14T23:59:00+00:00'))
    assert result['effective_at']=='2026-10-15T07:59:00+08:00'
    assert result['latest_completed']=='2026-10-14'


def test_cli_requires_existing_calendar_and_output(tmp_path):
    result=CliRunner().invoke(market_window_command,['--calendar-db',str(tmp_path/'missing.db'),'--as-of','2026-10-14','--output',str(tmp_path/'window.json')])
    assert result.exit_code!=0
    assert not (tmp_path/'missing.db').exists()
    calendar=seed(tmp_path)
    result=CliRunner().invoke(market_window_command,['--calendar-db',str(calendar.db_path),'--as-of','2026-10-14'])
    assert result.exit_code!=0


def test_active_wal_commits_are_visible_without_business_writes(tmp_path, monkeypatch):
    calendar = seed(tmp_path)
    monkeypatch.setattr(calendar, 'fetcher', lambda: pytest.fail('不得联网'))
    monkeypatch.setattr(calendar, 'refresh', lambda: pytest.fail('不得刷新'))
    with closing(sqlite3.connect(calendar.db_path)) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        writer.execute('PRAGMA wal_autocheckpoint=0')
        original_days = writer.execute('SELECT days FROM radar_calendar_cache').fetchone()[0]
        old_days = [day for day in json.loads(original_days) if day != '2026-10-15']
        writer.execute('UPDATE radar_calendar_cache SET days=?', (json.dumps(old_days),))
        writer.commit()
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        old = prepare_market_window(calendar, AS_OF, now=NOW)
        before_main = calendar.db_path.read_bytes()
        writer.execute("UPDATE radar_calendar_cache SET days=?, source='synthetic:new-wal:XSHG'", (original_days,))
        writer.commit()
        assert calendar.db_path.read_bytes() == before_main
        before_row = writer.execute('SELECT * FROM radar_calendar_cache').fetchone()
        result = prepare_market_window(calendar, AS_OF, now=NOW)
        assert result['latest_completed'] == '2026-10-15'
        assert old['latest_completed'] == '2026-10-14'
        assert result['calendar']['source'] == 'synthetic:new-wal:XSHG'
        assert result['calendar']['snapshot_sha256'] != old['calendar']['snapshot_sha256']
        assert writer.execute('SELECT * FROM radar_calendar_cache').fetchone() == before_row
        assert calendar.db_path.read_bytes() == before_main

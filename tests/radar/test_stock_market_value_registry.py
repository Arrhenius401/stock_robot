"""登记来源只读校验和历史边界。"""
import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from radar.stocks import market_value_registry as registry

DAY = date(2026, 10, 18)


def make_registry(tmp_path, monkeypatch):
    root = tmp_path / 'stock-market-value'
    root.mkdir()
    days = [f'2026-08-{day:02d}' for day in range(1, 31)] + [f'2026-09-{day:02d}' for day in range(1, 31)]
    window = {'days': days, 'calendar': {'snapshot_sha256': 'c' * 64}}
    def write(name, value):
        data = json.dumps(value).encode()
        (root / name).write_bytes(data)
        return {'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    scope = write('scope.json', {})
    market_window = write('window.json', window)
    artifact = write('body.json', {}) | {'fetched_at': None, 'time_basis': 'not_recorded',
                                        'source_url': 'https://datacenter-web.eastmoney.com/api/data/v1/get'}
    manifest = {'version': 'stock-market-value-registry-v1', 'as_of': DAY.isoformat(),
                'scope': scope, 'window': market_window, 'companies': {'600900': [artifact]}}
    path = root / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    monkeypatch.setattr(registry, 'prepare_market_window', lambda *a, **kw: window)
    monkeypatch.setattr(registry, 'evaluate_supplier_market_value', lambda **kw: {
        'symbol': kw['symbol'], 'as_of': kw['as_of'].isoformat(), 'status': 'current',
        'checks': [{'code': 'capitalization_current', 'outcome': 'passed'}], 'source': {'basis': 'supplier'}})
    return root, manifest


def test_missing_is_read_only(tmp_path):
    result = registry.load_market_value_source(tmp_path, '600900', DAY)
    assert result['status'] == 'unavailable' and not list(tmp_path.iterdir())


@pytest.mark.parametrize('body', ['[' * 50000 + ']' * 50000, '{"version":"a","version":"b"}'], ids=['deep', 'duplicate'])
def test_damaged_manifest_fails_closed(tmp_path, body):
    root = tmp_path / 'stock-market-value'
    root.mkdir()
    (root / 'manifest.json').write_text(body, encoding='utf-8')
    assert registry.load_market_value_source(tmp_path, '600900', DAY)['status'] == 'unavailable'


def test_current_historical_and_no_path_leak(tmp_path, monkeypatch):
    make_registry(tmp_path, monkeypatch)
    current = registry.load_market_value_source(tmp_path, '600900', DAY)
    assert current['status'] == 'current' and current['evaluation_as_of'] == DAY.isoformat()
    assert str(tmp_path) not in json.dumps(current)
    old = registry.load_market_value_source(tmp_path, '600900', date(2026, 10, 19))
    assert old['status'] == 'historical' and old['as_of'] == DAY.isoformat()
    assert old['checks'][0]['outcome'] == 'passed'
    assert registry.load_market_value_source(tmp_path, '600900', date(2026, 10, 17))['status'] == 'unavailable'


@pytest.mark.parametrize('damage', ['artifact', 'window', 'escape', 'symlink', 'calendar', 'manifest'])
def test_bad_sources_fail_closed(tmp_path, monkeypatch, damage):
    root, manifest = make_registry(tmp_path, monkeypatch)
    if damage == 'artifact':
        (root / 'body.json').write_text('changed')
    elif damage == 'window':
        (root / 'window.json').write_text('changed')
    elif damage == 'escape':
        manifest['companies']['600900'][0]['path'] = '../outside'
    elif damage == 'symlink':
        (root / 'body.json').unlink()
        try:
            (root / 'body.json').symlink_to(tmp_path / 'outside')
        except OSError:
            pytest.skip('当前环境无创建符号链接权限')
    elif damage == 'calendar':
        monkeypatch.setattr(registry, 'prepare_market_window', lambda *a, **kw: {
            'days': ['2026-01-01'], 'calendar': {'snapshot_sha256': 'd' * 64}})
    else:
        (root / 'manifest.json').write_text('{')
    if damage == 'escape':
        (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    result = registry.load_market_value_source(tmp_path, '600900', DAY)
    assert result['status'] == 'unavailable'
    assert str(tmp_path) not in json.dumps(result)


def test_manifest_change_during_evaluation_fails(tmp_path, monkeypatch):
    root, _ = make_registry(tmp_path, monkeypatch)
    def change(**kwargs):
        (root / 'manifest.json').write_text('{}')
        return {'status': 'current', 'checks': [{'outcome': 'passed'}]}
    monkeypatch.setattr(registry, 'evaluate_supplier_market_value', change)
    assert registry.load_market_value_source(tmp_path, '600900', DAY)['status'] == 'unavailable'


def test_real_calendar_cache_detects_mismatched_snapshot(tmp_path, monkeypatch):
    original = registry.prepare_market_window
    root, manifest = make_registry(tmp_path, monkeypatch)
    monkeypatch.setattr(registry, 'prepare_market_window', original)
    target = date(2020, 4, 30)
    days = [(target - timedelta(days=i)).isoformat() for i in range(100, -1, -1)]
    with sqlite3.connect(tmp_path / 'radar_collector.db') as connection:
        connection.execute('CREATE TABLE radar_calendar_cache '
                           '(market TEXT, days TEXT, coverage_start TEXT, coverage_end TEXT, fetched_at TEXT, source TEXT)')
        connection.execute('INSERT INTO radar_calendar_cache VALUES (?, ?, ?, ?, ?, ?)',
                           ('XSHG', json.dumps(days), days[0], days[-1],
                            datetime(2020, 1, 1, tzinfo=UTC).isoformat(), 'test-calendar'))
    calendar = registry.TradingCalendar(tmp_path / 'radar_collector.db', refresh_enabled=False)
    window = original(calendar, target, now=datetime.now().astimezone())
    payload = json.dumps(window).encode()
    (root / 'window.json').write_bytes(payload)
    manifest['as_of'] = target.isoformat()
    manifest['window']['sha256'] = hashlib.sha256(payload).hexdigest()
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    assert registry.load_market_value_source(tmp_path, '600900', target)['status'] == 'current'
    with sqlite3.connect(tmp_path / 'radar_collector.db') as connection:
        connection.execute("UPDATE radar_calendar_cache SET source = 'changed'")
    assert registry.load_market_value_source(tmp_path, '600900', target)['status'] == 'unavailable'

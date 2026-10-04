"""海外指数的真实短样本与时间边界验证。"""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from radar.overseas import OverseasService, close_at, parse_daily
from radar.universe import UniverseRepository

ROOT = Path(__file__).parents[2]


def sample(source, code):
    return json.loads((ROOT / 'tests/resources/radar' / f'{source}-{code.lower()}-daily.json').read_text(encoding='utf-8'))


def fixture_service(tmp_path, monkeypatch):
    service = OverseasService(tmp_path / 'radar.db')
    calls = []

    def fetch(mapping):
        calls.append(mapping['code'])
        return sample(mapping['source'], mapping['code'].lstrip('^'))

    monkeypatch.setattr(service, '_fetch', fetch)
    return service, calls


def universe():
    return UniverseRepository(ROOT / 'config/radar_universes').get('overseas_etf')


def now():
    return datetime(2026, 10, 3, 18, tzinfo=ZoneInfo('Asia/Shanghai'))


def snapshot():
    return {'as_of_date': '2026-09-30', 'items': [{'symbol': i.symbol, 'observed_at': '2026-10-03T15:00:00+08:00', 'market_date': '2026-09-30', 'status': 'fresh'} for i in universe().instruments]}


def test_actual_sources_reuse_and_bases(tmp_path, monkeypatch):
    service, calls = fixture_service(tmp_path, monkeypatch)
    result = service.refresh(universe(), snapshot(), now())
    rows = {r['symbol']: r for r in result['items']}
    assert calls.count('N225') == 1
    assert rows['513500']['base_date'] == '2026-09-29'
    assert rows['513520']['base_date'] == '2026-09-30'
    assert rows['513500']['as_of_date'] == '2026-10-02'
    assert rows['513500']['cumulative_change'] == pytest.approx(7722.72 / 7670.84 - 1)
    assert rows['513350']['cumulative_change'] is None
    assert rows['513350']['daily_change'] is None
    assert rows['513350']['error']
    assert rows['513030']['status'] == 'unavailable'


def test_cached_failure_and_historical_read_only(tmp_path, monkeypatch):
    service, calls = fixture_service(tmp_path, monkeypatch)
    service.refresh(universe(), snapshot(), now())
    def fail(mapping):
        raise OSError('offline')
    monkeypatch.setattr(service, '_fetch', fail)
    rows = service.refresh(universe(), snapshot(), now())['items']
    assert rows[0]['status'] == 'stale'
    assert rows[0]['as_of_date'] == '2026-10-02'
    assert '连接失败' in rows[0]['error']
    assert 'offline' not in rows[0]['error']
    old = snapshot()
    old['items'][0]['market_date'] = '2026-09-28'
    old['items'][0]['status'] = 'stale'
    view = service.view(universe(), old, now())
    assert view['items'][0]['base_date'] == '2026-09-25'
    assert len(calls) == 5


def test_identity_and_unfinished_close(tmp_path):
    service = OverseasService(tmp_path / 'radar.db')
    mapping = service.mappings['513100']
    payload = sample('yahoo', 'NDX')
    payload['chart']['result'][0]['meta']['currency'] = 'JPY'
    with pytest.raises(ValueError, match='身份'):
        parse_daily(mapping, payload, now())
    mapping = service.mappings['513500']
    rows = parse_daily(mapping, sample('eastmoney', 'SPX'), datetime(2026, 10, 2, 23, tzinfo=ZoneInfo('Asia/Shanghai')))
    assert rows[-1]['date'] == '2026-10-01'
    assert close_at('2026-07-01', service.mappings['513100']).hour == 16
    summer = close_at('2026-07-01', service.mappings['513100']).utcoffset()
    winter = close_at('2026-12-01', service.mappings['513100']).utcoffset()
    assert summer is not None and summer.total_seconds() == -14400
    assert winter is not None and winter.total_seconds() == -18000


def test_missing_snapshot_and_unmapped(tmp_path, monkeypatch):
    service, _ = fixture_service(tmp_path, monkeypatch)
    result = service.refresh(universe(), None, now())
    assert result['items'][0]['cumulative_change'] is None
    service.mappings.pop('513500')
    assert service.view(universe(), None, now())['items'][0]['status'] == 'unmapped'


@pytest.mark.parametrize('field,value', [('symbol', '^IXIC'), ('instrumentType', 'ETF'),
                                       ('exchangeTimezoneName', 'Asia/Tokyo'), ('longName', 'NASDAQ Composite'),
                                       ('dataGranularity', '1h')])
def test_yahoo_rejects_wrong_identity(tmp_path, field, value):
    service = OverseasService(tmp_path / 'radar.db')
    payload = sample('yahoo', 'NDX')
    payload['chart']['result'][0]['meta'][field] = value
    with pytest.raises(ValueError, match='身份'):
        parse_daily(service.mappings['513100'], payload, now())


def test_future_invalid_and_timezone_boundaries(tmp_path, monkeypatch):
    service, _ = fixture_service(tmp_path, monkeypatch)
    payload = sample('eastmoney', 'SPX')
    payload['data']['klines'].append('2026-10-06,100,100,100,100,0,0')
    rows = parse_daily(service.mappings['513500'], payload, now())
    assert rows[-1]['date'] == '2026-10-02'
    payload['data']['code'] = 'NDX'
    with pytest.raises(ValueError, match='身份'):
        parse_daily(service.mappings['513500'], payload, now())
    with pytest.raises(ValueError, match='时区'):
        service.view(universe(), None, datetime(2026, 10, 3))  # noqa: DTZ001 — 验证拒绝无时区输入
    service.refresh(universe(), snapshot(), now())
    future = snapshot()
    future['items'][0]['market_date'] = '2026-10-06'
    assert service.view(universe(), future, now())['items'][0]['cumulative_change'] is None
    summer = close_at('2026-07-01', service.mappings['513080']).utcoffset()
    winter = close_at('2026-12-01', service.mappings['513080']).utcoffset()
    assert summer is not None and summer.total_seconds() == 7200
    assert winter is not None and winter.total_seconds() == 3600


def test_mapping_version_and_missing_history_do_not_reuse(tmp_path, monkeypatch):
    service, _ = fixture_service(tmp_path, monkeypatch)
    service.refresh(universe(), snapshot(), now())
    service.version += 1
    assert service.view(universe(), snapshot(), now())['items'][0]['status'] == 'unavailable'


def test_legacy_collection_timestamp_never_becomes_market_date(tmp_path, monkeypatch):
    service, _ = fixture_service(tmp_path, monkeypatch)
    service.refresh(universe(), snapshot(), now())
    old = snapshot()
    row = old['items'][0]
    row.pop('market_date')
    row['source_as_of_date'] = '2026-09-30'
    row['status'] = 'stale'
    assert service.view(universe(), old, now())['items'][0]['base_date'] == '2026-09-29'
    row.pop('source_as_of_date')
    assert service.view(universe(), old, now())['items'][0]['cumulative_change'] is None
    row['status'] = 'fresh'
    assert service.view(universe(), old, now())['items'][0]['base_date'] == '2026-09-29'


def test_domestic_pool_is_not_applicable_without_network(tmp_path, monkeypatch):
    service, calls = fixture_service(tmp_path, monkeypatch)
    domestic = UniverseRepository(ROOT / 'config/radar_universes').get('cn_hk_etf')
    result = service.refresh(domestic, None, now())
    assert result['status'] == 'not_applicable'
    assert result['items'] == []
    assert result['reason']
    assert calls == []

"""同业范围产物：完整性校验、v2 排名绑定与拒绝小范围。"""
from __future__ import annotations

import json
from datetime import date

import pytest

from radar.stocks.index_scope import INDEX_SOURCES, POOL_INDEX_CODES
from radar.stocks.industry_scale import (
    build_peer_scale,
    load_industry_scale,
    save_industry_scale,
)
from radar.stocks.peer_universe import (
    build_peer_universe,
    load_peer_universe,
    save_peer_universe,
)

DAY = date(2026, 11, 7)
_SHA = 'a' * 64


def _symbols(prefix: int, count: int) -> list[str]:
    return [f'{prefix + index:06d}' for index in range(count)]


def _scope_document() -> dict:
    plan = [('000510', 500), ('000922', 100), ('H30269', 50), ('000688', 50), ('399006', 100)]
    items, companies = [], []
    for code, count in plan:
        expected = INDEX_SOURCES[code]['count']
        assert expected == count
        prefix = {'000510': 600000, '000922': 601000, 'H30269': 603000, '000688': 605000, '399006': 300000}[code]
        rows = []
        for symbol in _symbols(prefix, count):
            rows.append({'symbol': symbol, 'name': f'公司{symbol}'})
            companies.append({'symbol': symbol, 'name': f'公司{symbol}', 'source_indexes': [code],
                              'pool_ids': sorted(pool for pool, codes in POOL_INDEX_CODES.items() if code in codes)})
        items.append({'index_code': code, 'expected_count': expected, 'row_count': expected, 'complete': True,
                      'rows': rows, 'scope_exclusions': [], 'snapshot_date': DAY.isoformat()})
    pools = {}
    for pool, codes in POOL_INDEX_CODES.items():
        members = sorted(row['symbol'] for row in companies if pool in row['pool_ids'])
        pools[pool] = {'index_codes': list(codes), 'status': 'complete', 'complete': True, 'companies': members}
    return {'version': 'official-index-scope-v1', 'as_of': DAY.isoformat(), 'status': 'complete',
            'scope_complete': True, 'items': items, 'companies': companies, 'pools': pools}


@pytest.fixture(scope='module')
def document() -> dict:
    return _scope_document()


def _build(document: dict) -> dict:
    return build_peer_universe(document, source_run_id='run-1', source_scope_sha256=_SHA,
                               industry_mapping_sha256='b' * 64, created_at='2026-11-07T10:00:00+08:00')


def test_peer_universe_covers_full_scope(document):
    payload = _build(document)
    assert payload['company_count'] == 800
    assert payload['company_count'] == len(payload['companies'])
    assert [row['symbol'] for row in payload['companies']] == sorted(row['symbol'] for row in payload['companies'])
    assert payload['universe'] == 'index_scope_union_v1'
    assert len({payload['scope_sha256'], payload['universe_sha256'], payload['source_scope_sha256']}) == 3


def test_peer_universe_rejects_incomplete_index_rows(document):
    broken = json.loads(json.dumps(document))
    broken['items'][0]['expected_count'] = 10
    with pytest.raises(ValueError):
        _build(broken)


def test_peer_universe_rejects_company_mismatch(document):
    broken = json.loads(json.dumps(document))
    broken['companies'] = broken['companies'][:-1]
    with pytest.raises(ValueError):
        _build(broken)


def test_peer_universe_roundtrip_and_tamper(tmp_path, document):
    path = tmp_path / 'stock-leadership' / 'peer-universe.json'
    save_peer_universe(path, _build(document))
    loaded = load_peer_universe(tmp_path)
    assert loaded['status'] == 'current' and loaded['company_count'] == 800

    payload = json.loads(path.read_text(encoding='utf-8'))
    payload['company_count'] = 799
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    assert load_peer_universe(tmp_path)['status'] == 'unavailable'


def test_peer_universe_missing_is_unavailable(tmp_path):
    assert load_peer_universe(tmp_path)['status'] == 'unavailable'


def _rows() -> list[dict]:
    return [{'symbol': f'60000{index}', 'industry': '半导体', 'market_cap_yuan': str(index * 1000),
             'market_cap_date': '2026-11-06', 'market_value_manifest_sha256': 'c' * 64}
            for index in range(1, 7)]


def _peer_scale():
    return build_peer_scale(_rows(), as_of=DAY, peer_universe_sha256='d' * 64, company_count=6,
                            industry_mapping_sha256='e' * 64, scope_sha256='f' * 64,
                            window_sha256='1' * 64)


def test_peer_scale_requires_uniform_date_and_full_coverage():
    value = _peer_scale()
    assert value['version'] == 'industry-scale-v2'
    assert value['market_cap_date'] == '2026-11-06'
    assert value['symbols']['600006']['rank'] == 1

    mixed = _rows()
    mixed[0]['market_cap_date'] = '2026-11-05'
    with pytest.raises(ValueError):
        build_peer_scale(mixed, as_of=DAY, peer_universe_sha256='d' * 64, company_count=6,
                         industry_mapping_sha256='e' * 64, scope_sha256='f' * 64, window_sha256='1' * 64)

    missing = _rows()
    missing[0]['market_cap_yuan'] = ''
    with pytest.raises(ValueError):
        build_peer_scale(missing, as_of=DAY, peer_universe_sha256='d' * 64, company_count=6,
                         industry_mapping_sha256='e' * 64, scope_sha256='f' * 64, window_sha256='1' * 64)


def test_peer_scale_roundtrip_and_rejections(tmp_path):
    value = _peer_scale()
    path = tmp_path / 'stock-leadership' / 'scale.json'
    save_industry_scale(path, value)
    assert load_industry_scale(tmp_path, '600005', DAY)['status'] == 'current'
    assert load_industry_scale(tmp_path, '600005', date(2026, 11, 8))['status'] == 'unavailable'

    broken = json.loads(path.read_text(encoding='utf-8'))
    broken['symbols']['600005']['market_cap_date'] = '2026-11-05'
    path.write_text(json.dumps(broken, ensure_ascii=False), encoding='utf-8')
    assert load_industry_scale(tmp_path, '600005', DAY)['status'] == 'unavailable'

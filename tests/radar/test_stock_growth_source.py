"""成长原始来源登记不信任派生结果。"""
import hashlib
import json
from datetime import date

import pytest

from radar.stocks.growth_source import load_growth_source
from radar.stocks.index_scope import POOL_INDEX_CODES
from tests.radar.test_stock_growth_research import rows


def prepare(tmp_path):
    root = tmp_path / 'stock-growth'
    root.mkdir()
    def write(name, payload):
        raw = json.dumps(payload).encode()
        (root / name).write_bytes(raw)
        return {'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    scope = write('scope.json', {'version': 'official-index-scope-v1', 'as_of': '2025-10-16',
        'status': 'complete', 'scope_complete': True,
        'companies': [{'symbol': '600900', 'name': '长江电力', 'source_indexes': ['000510'],
                       'pool_ids': list(POOL_INDEX_CODES)}],
        'pools': {pool: {'status': 'complete', 'complete': True, 'index_codes': list(codes),
                         'companies': ['600900']} for pool, codes in POOL_INDEX_CODES.items()}})
    data = rows()
    ref = write('main.raw', {'success': True, 'result': {'pages': 1, 'count': len(data), 'data': data}})
    ref.update(fetched_at='2025-10-18T12:00:00+08:00',
               source_url='https://datacenter.eastmoney.com/securities/api/data/get',
               request_params={'type': 'RPT_F10_FINANCE_MAINFINADATA', 'filter': '(SECUCODE="600900.SH")'})
    manifest = {'version': 'stock-growth-source-v1', 'as_of': '2025-10-18', 'scope': scope,
                'companies': {'600900': ref}}
    write('manifest.json', manifest)
    return root, manifest


def test_missing_does_not_create_state(tmp_path):
    assert load_growth_source(tmp_path, '600900', date(2025, 10, 18))['status'] == 'unavailable'
    assert not list(tmp_path.iterdir())


def test_recomputed_current_and_historical(tmp_path):
    prepare(tmp_path)
    result = load_growth_source(tmp_path, '600900', date(2025, 10, 18))
    assert result['status'] == 'current'
    assert result['research']['paths']['sustained_growth'] == 'met_reference'
    assert load_growth_source(tmp_path, '600900', date(2025, 10, 19))['status'] == 'historical'
    assert str(tmp_path) not in json.dumps(result)


@pytest.mark.parametrize('damage', ['raw', 'escape', 'identity', 'future', 'scope'])
def test_damaged_source_is_unavailable(tmp_path, damage):
    root, manifest = prepare(tmp_path)
    if damage == 'raw':
        (root / 'main.raw').write_text('{}')
    elif damage == 'scope':
        (root / 'scope.json').write_text('{}')
    elif damage == 'escape':
        manifest['companies']['600900']['path'] = '../main.raw'
    elif damage == 'identity':
        manifest['companies']['600900']['request_params']['filter'] = '(SECUCODE="000001.SZ")'
    else:
        manifest['companies']['600900']['fetched_at'] = '2099-01-01T00:00:00+08:00'
    (root / 'manifest.json').write_text(json.dumps(manifest))
    assert load_growth_source(tmp_path, '600900', date(2025, 10, 18))['status'] == 'unavailable'


@pytest.mark.parametrize('records', [[None], [{'SECURITY_CODE': '600900', 'SECUCODE': '000001.SZ',
    'REPORT_DATE': '2025-06-30', 'NOTICE_DATE': '2025-08-20'}]])
def test_malformed_or_conflicting_identity_fails_closed(tmp_path, records):
    root, manifest = prepare(tmp_path)
    raw = json.dumps({'success': True, 'result': {'pages': 1, 'count': len(records), 'data': records}}).encode()
    (root / 'main.raw').write_bytes(raw)
    manifest['companies']['600900'].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (root / 'manifest.json').write_text(json.dumps(manifest))
    assert load_growth_source(tmp_path, '600900', date(2025, 10, 18))['status'] == 'unavailable'

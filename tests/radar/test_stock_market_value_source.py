import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from radar.stocks.index_scope import POOL_INDEX_CODES
from radar.stocks.market_value_source import evaluate_supplier_market_value

URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'


def write_json(path: Path, obj: object) -> tuple[Path, str]:
    raw = json.dumps(obj, ensure_ascii=False).encode()
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def inputs(tmp_path: Path) -> dict:
    days = [date(2026, 7, 1) + timedelta(days=i) for i in range(60)]
    cutoff = days[-1]
    scope_path, scope_sha = write_json(tmp_path / 'scope.json', {
        'version': 'official-index-scope-v1', 'status': 'complete', 'scope_complete': True, 'as_of': '2026-06-30',
        'companies': [{'symbol': '600900', 'name': '长江电力', 'source_indexes': ['000510'],
                       'pool_ids': ['dividend_value', 'growth_trend']}],
        'pools': {pool: {'status': 'complete', 'complete': True, 'index_codes': list(codes),
                         'companies': ['600900']} for pool, codes in POOL_INDEX_CODES.items()},
    })
    window_path, window_sha = write_json(tmp_path / 'window.json', {
        'version': 'stock-market-window-v1', 'market': 'XSHG', 'as_of': cutoff.isoformat(),
        'research_only': True, 'formal_screening_enabled': False,
        'days': [d.isoformat() for d in days], 'latest_completed': cutoff.isoformat(),
        'calendar': {'source': 'test', 'fetched_at': '2026-06-30T00:00:00+08:00',
                     'coverage_start': '2026-01-01', 'coverage_end': '2026-12-31', 'snapshot_sha256': 'a' * 64},
    })
    rows = [{'SECURITY_CODE': '600900', 'TRADE_DATE': d.isoformat() + ' 00:00:00',
             'TOTAL_MARKET_CAP': 20000000000} for d in days]
    body, sha = write_json(tmp_path / 'raw.json', {'success': True, 'result': {'pages': 1, 'count': 60, 'data': rows}})
    artifact = {'path': body, 'sha256': sha, 'bytes': body.stat().st_size,
                'fetched_at': '2026-10-27T00:00:00+08:00', 'source_url': URL}
    return {'symbol': '600900', 'as_of': cutoff, 'scope_path': scope_path, 'scope_sha256': scope_sha,
            'window_path': window_path, 'window_sha256': window_sha, 'artifacts': [artifact]}


def checks(result: dict) -> dict:
    return {x['code']: x['outcome'] for x in result['checks']}


def test_complete_threshold_and_trace(tmp_path: Path) -> None:
    result = evaluate_supplier_market_value(**inputs(tmp_path))
    assert checks(result) == {'capitalization_current': 'passed', 'capitalization_average_60d': 'passed'}
    assert result['source']['basis'] == 'eastmoney_supplier_total_market_cap'
    assert result['source']['artifacts'][0]['fetched_at'] == '2026-10-27T00:00:00+08:00'


def test_future_capture_time_is_unavailable(tmp_path: Path) -> None:
    args = inputs(tmp_path)
    args['artifacts'][0]['fetched_at'] = '2099-01-01T00:00:00+08:00'
    result = evaluate_supplier_market_value(**args)
    assert result['status'] == 'unavailable'
    assert set(checks(result).values()) == {'pending_verification'}


def test_average_display_rounding_does_not_change_floor_comparison(tmp_path: Path) -> None:
    args = inputs(tmp_path)
    artifact = args['artifacts'][0]
    body = json.loads(artifact['path'].read_bytes())
    body['result']['data'][0]['TOTAL_MARKET_CAP'] = 19999999999.99
    path, artifact['sha256'] = write_json(artifact['path'], body)
    artifact['bytes'] = path.stat().st_size
    result = evaluate_supplier_market_value(**args)
    average = next(row for row in result['checks'] if row['code'] == 'capitalization_average_60d')
    assert average['outcome'] == 'failed'
    assert average['values']['average_amount_yuan'] == '20000000000.00'


def test_tampering_and_scope_identity_fail_closed(tmp_path: Path) -> None:
    args = inputs(tmp_path)
    args['artifacts'][0]['path'].write_bytes(b'changed')
    result = evaluate_supplier_market_value(**args)
    assert set(checks(result).values()) == {'pending_verification'}
    args = inputs(tmp_path)
    args['symbol'] = '600036'
    assert set(checks(evaluate_supplier_market_value(**args)).values()) == {'pending_verification'}


@pytest.mark.parametrize('change', ['duplicate', 'missing', 'bad_number', 'wrong_identity'])
def test_invalid_or_missing_rows_never_allow_average(tmp_path: Path, change: str) -> None:
    args = inputs(tmp_path)
    artifact = args['artifacts'][0]
    data = json.loads(artifact['path'].read_bytes())
    rows = data['result']['data']
    if change == 'duplicate':
        rows[-2] = rows[-1].copy()
    elif change == 'missing':
        rows.pop(0)
    elif change == 'bad_number':
        rows[0]['TOTAL_MARKET_CAP'] = None
    else:
        rows[0]['SECURITY_CODE'] = '600036'
    data['result']['count'] = len(rows)
    path, artifact['sha256'] = write_json(artifact['path'], data)
    artifact['bytes'] = path.stat().st_size
    assert checks(evaluate_supplier_market_value(**args))['capitalization_average_60d'] == 'pending_verification'


def test_split_artifacts_and_below_floor(tmp_path: Path) -> None:
    args = inputs(tmp_path)
    artifact = args['artifacts'][0]
    rows = json.loads(artifact['path'].read_bytes())['result']['data']
    for row in rows:
        row['TOTAL_MARKET_CAP'] = 19999999999
    args['artifacts'] = []
    for n, subset in enumerate((rows[:30], rows[30:])):
        path, sha = write_json(tmp_path / f'part{n}.json', {'success': True, 'result': {'pages': 1, 'count': len(subset), 'data': subset}})
        args['artifacts'].append({'path': path, 'sha256': sha, 'bytes': path.stat().st_size, 'fetched_at': artifact['fetched_at'], 'source_url': URL})
    assert set(checks(evaluate_supplier_market_value(**args)).values()) == {'failed'}


def test_explicit_unrecorded_capture_time_is_preserved(tmp_path: Path) -> None:
    args = inputs(tmp_path)
    args['artifacts'][0].update(fetched_at=None, time_basis='not_recorded')
    result = evaluate_supplier_market_value(**args)
    assert set(checks(result).values()) == {'passed'}
    assert result['source']['artifacts'][0]['fetched_at'] is None
    assert result['source']['artifacts'][0]['time_basis'] == 'not_recorded'


@pytest.mark.parametrize('change', [
    {'fetched_at': None},
    {'fetched_at': None, 'time_basis': 'unknown'},
    {'fetched_at': '2026-10-27T00:00:00'},
    {'fetched_at': '2026-06-01T00:00:00+08:00'},
    {'fetched_at': '2026-10-27T00:00:00+08:00', 'time_basis': 'not_recorded'},
])
def test_invalid_capture_times_are_unavailable(tmp_path: Path, change: dict) -> None:
    args = inputs(tmp_path)
    args['artifacts'][0].update(change)
    assert set(checks(evaluate_supplier_market_value(**args)).values()) == {'pending_verification'}

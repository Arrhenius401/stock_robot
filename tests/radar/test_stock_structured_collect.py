"""结构化薄采集器的预算、范围及缓存边界。"""
import json
from datetime import date
from pathlib import Path

import pytest

from radar.stocks.structured_collect import (
    collect_structured_company,
    collect_structured_company_result,
)


@pytest.fixture(autouse=True)
def _deny_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail('离线测试禁止默认请求')
    monkeypatch.setattr('radar.stocks.structured_collect._fetch', forbidden)


def _fake(calls, *, status=200, latest='2026-06-30'):
    def fetch(url, params):
        calls.append((url, params.copy()))
        if status != 200:
            return status, b'blocked'
        if url.endswith('/Index'):
            return 200, b'<input value="4" id="hidctype">'
        dates = [latest, '2025-12-31', '2025-06-30', '2024-12-31', '2023-12-31']
        if url.endswith('/get'):
            return 200, json.dumps({'result': {'data': [
                {'REPORT_DATE': d, 'NOTICE_DATE': '2026-08-30'} for d in dates]}}).encode()
        if url.endswith('DateAjaxNew'):
            return 200, json.dumps({'data': [{'REPORT_DATE': d} for d in dates]}).encode()
        return 200, ('{"data":[' + ','.join(
            '{"REPORT_DATE":"' + d + '","PARENT_NETPROFIT":123.4567890123456789}'
            for d in params['dates'].split(',')) + ']}').encode()
    return fetch


def test_targets_exact_bytes_and_cache(tmp_path):
    calls = []
    result = collect_structured_company('000100', tmp_path, as_of=date(2026, 10, 18), fetch=_fake(calls))
    assert result['status'] == 'complete'
    assert result['targets'] == ['2026-06-30', '2025-12-31', '2025-06-30', '2024-12-31', '2023-12-31']
    assert result['records']['profit'][0]['PARENT_NETPROFIT'] == '123.4567890123456789'
    assert len(calls) == 5
    assert result['point_in_time_verified'] is False
    again = collect_structured_company('000100', tmp_path, as_of=date(2026, 10, 18), fetch=_fake(calls))
    assert again['records'] == result['records']
    assert len(calls) == 5
    raw_path = result['sources']['profit']['raw_artifact']
    Path(raw_path).write_bytes(b'corrupt')
    collect_structured_company('000100', tmp_path, as_of=date(2026, 10, 18), fetch=_fake(calls))
    assert len(calls) == 6


def test_result_boundary_reuses_cache_and_original_time(tmp_path):
    calls = []
    first = collect_structured_company_result('000100', tmp_path, as_of=date(2026, 10, 18), fetch=_fake(calls))
    second = collect_structured_company_result('000100', tmp_path, as_of=date(2026, 10, 18), fetch=_fake(calls))
    assert len(calls) == 5
    assert first.record.result_state == second.record.result_state == 'complete'
    assert first.value is not None and second.value is not None
    before = first.value['collection_records']
    after = second.value['collection_records']
    assert len(before) == len(after) == 5
    assert all(row['cache_status'] == 'cache' for row in after)
    assert [row['collected_at'] for row in before] == [row['collected_at'] for row in after]


def test_429_stops_all_endpoints(tmp_path):
    calls = []
    result = collect_structured_company('600900', tmp_path, as_of=date(2026, 10, 18), fetch=_fake(calls, status=429))
    assert result['source_stopped'] is True
    assert len(calls) == 1
    assert result['status'] == 'partial'


def test_retry_budget_and_delay(tmp_path):
    calls, delays = [], []
    result = collect_structured_company('600900', tmp_path, as_of=date(2026, 10, 18), fetch=_fake(calls, status=503), sleep=delays.append)
    assert delays == [3, 10] * 2
    assert len(calls) == 6
    assert result['errors']


def test_date_newer_than_main_is_not_silently_downgraded(tmp_path):
    calls = []
    original = _fake(calls)
    def fetch(url, params):
        if url.endswith('DateAjaxNew'):
            return 200, b'{"data":[{"REPORT_DATE":"2026-09-30"}]}'
        if url.endswith('AjaxNew'):
            return 200, b'{"data":[]}'
        return original(url, params)
    result = collect_structured_company('000100', tmp_path, as_of=date(2026, 10, 18), fetch=fetch)
    assert '2026-09-30' in result['targets']
    assert result['status'] == 'partial'
    assert result['latest_available_period'] == '2026-09-30'


def test_missing_annual_does_not_shift_three_year_window(tmp_path):
    calls = []
    original = _fake(calls)
    def fetch(url, params):
        if url.endswith(('/get', 'DateAjaxNew')):
            rows = [{'REPORT_DATE': d, 'NOTICE_DATE': '2025-10-30'} for d in
                    ['2025-09-30', '2024-12-31', '2023-12-31', '2022-12-31']]
            return 200, json.dumps({'result': {'data': rows}} if url.endswith('/get') else {'data': rows}).encode()
        return original(url, params)
    result = collect_structured_company('000100', tmp_path, as_of=date(2026, 2, 18), fetch=fetch)
    assert result['targets'] == ['2025-12-31', '2025-09-30', '2024-12-31', '2024-09-30', '2023-12-31']


def test_redirect_not_followed_or_retried(tmp_path):
    calls = []
    result = collect_structured_company('000100', tmp_path, as_of=date(2026, 10, 18), fetch=_fake(calls, status=302))
    assert len(calls) == 2
    assert result['status'] == 'partial'
    assert any('重定向' in message for message in result['errors'])


def test_unknown_internal_endpoint_is_rejected():
    from radar.stocks.structured_collect import _http
    with pytest.raises(ValueError, match='端点'):
        _http('https://example.com/report.pdf', {})


@pytest.mark.parametrize('bad', [[], {'fetched_at': '2026-10-18T00:00:00'}, {'bytes': '10'}])
def test_malformed_cache_metadata_is_refetched(tmp_path, bad):
    calls = []
    result = collect_structured_company('000100', tmp_path, as_of=date(2026, 10, 18), fetch=_fake(calls))
    raw = Path(result['sources']['main']['raw_artifact'])
    index = next(path for path in tmp_path.glob('*.json') if json.loads(path.read_bytes())['raw_artifact'] == str(raw))
    meta = json.loads(index.read_bytes())
    index.write_text(json.dumps({**meta, **bad} if isinstance(bad, dict) else bad))
    collect_structured_company('000100', tmp_path, as_of=date(2026, 10, 18), fetch=_fake(calls))
    assert len(calls) == 6


@pytest.mark.parametrize('payload', [b'{"success":false,"result":{"data":[]}}', b'{"data":[],"data":[]}'])
def test_reject_failed_or_duplicate_json_payload(payload):
    from radar.stocks.structured_collect import _rows
    with pytest.raises(ValueError):
        _rows(payload, main=b'success' in payload)


@pytest.mark.parametrize('symbol', ['888888', '123456', '510300'])
def test_reject_outside_confirmed_stock_universe(tmp_path, symbol):
    with pytest.raises(ValueError):
        collect_structured_company(symbol, tmp_path, as_of=date(2026, 10, 18), fetch=lambda *_: pytest.fail('不可请求'))


@pytest.mark.parametrize('as_of', [date(9999, 1, 1), date(1, 1, 1), '2026-10-18'])
def test_reject_invalid_capture_date(tmp_path, as_of):
    with pytest.raises(ValueError):
        collect_structured_company('000100', tmp_path, as_of=as_of, fetch=lambda *_: pytest.fail('不可请求'))


def test_symlink_parent_rejected_before_fetch(tmp_path, monkeypatch):
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == tmp_path or original(path))
    with pytest.raises(ValueError, match='符号链接'):
        collect_structured_company('000100', tmp_path / 'nested', as_of=date(2026, 10, 18))


def test_missing_python312_junction_api_supported(tmp_path, monkeypatch):
    from radar.stocks.structured_collect import _safe_path
    monkeypatch.delattr(Path, 'is_junction', raising=False)
    _safe_path(tmp_path)


def test_python311_junction_fallback_rejects_mount_point(tmp_path, monkeypatch):
    import stat
    from types import SimpleNamespace

    from radar.stocks.structured_collect import _safe_path
    original = Path.lstat
    monkeypatch.delattr(Path, 'is_junction', raising=False)
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(st_mode=stat.S_IFDIR,
        st_reparse_tag=0xA0000003) if path == tmp_path else original(path))
    with pytest.raises(ValueError, match='目录联接'):
        _safe_path(tmp_path / 'not-created')


def test_refresh_different_content_preserves_prior_archive(tmp_path):
    calls = []
    first = collect_structured_company('000100', tmp_path, as_of=date(2026, 10, 18), fetch=_fake(calls))
    old = Path(first['sources']['profit']['raw_artifact'])
    old_bytes = old.read_bytes()
    original = _fake(calls)
    def fetch(url, params):
        status, raw = original(url, params)
        return status, raw.replace(b'123.4567890123456789', b'999.4567890123456789') if url.endswith('lrbAjaxNew') else raw
    refreshed = collect_structured_company('000100', tmp_path, as_of=date(2026, 10, 18), refresh=True, fetch=fetch)
    assert refreshed['sources']['profit']['raw_artifact'] != str(old)
    assert old.read_bytes() == old_bytes
    import hashlib
    assert hashlib.sha256(old_bytes).hexdigest() == first['sources']['profit']['content_sha256']


@pytest.mark.parametrize('symbol', ['https://example.com/report.pdf', '../000100', 'ABCDEF'])
def test_no_arbitrary_target_or_pdf(tmp_path, symbol):
    with pytest.raises(ValueError):
        collect_structured_company(symbol, tmp_path, as_of=date(2026, 10, 18), fetch=lambda *_: pytest.fail('不可请求'))

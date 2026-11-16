"""风险警示列表仅作为采集时点研究线索。"""
import hashlib
import json
from datetime import date

import pytest


def snapshot(tmp_path, *, rows=None, as_of='2026-11-03'):
    root = tmp_path / 'stock-risk-warning'
    root.mkdir()
    rows = rows if rows is not None else [{'f12': '600000', 'f13': 1, 'f14': 'ST样本'}]
    raw = json.dumps({'rc': 0, 'data': {'total': len(rows), 'diff': rows}}).encode()
    (root / 'page.json').write_bytes(raw)
    manifest = {'version': 'risk-warning-v2', 'as_of': as_of,
                'source_url': 'https://40.push2.eastmoney.com/api/qt/clist/get',
                'fetched_at': '2026-11-03T16:00:00+08:00',
                'pages': [{'path': 'page.json', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]}
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return root


def test_risk_warning_is_a_clue_not_a_governance_decision(tmp_path):
    from radar.stocks.risk_warning import load_risk_warning_source

    snapshot(tmp_path)
    match = load_risk_warning_source(tmp_path, '600000', date(2026, 11, 3))
    absent = load_risk_warning_source(tmp_path, '600001', date(2026, 11, 3))
    assert match['status'] == 'current' and match['observation'] == 'observed'
    assert absent['status'] == 'current' and absent['observation'] == 'not_observed'
    assert match['reviewed'] is False and match['formal_screening_enabled'] is False


def test_risk_warning_is_historical_or_unavailable_at_other_cutoffs(tmp_path):
    from radar.stocks.risk_warning import load_risk_warning_source

    snapshot(tmp_path)
    assert load_risk_warning_source(tmp_path, '600000', date(2026, 11, 4))['status'] == 'historical'
    assert load_risk_warning_source(tmp_path, '600000', date(2026, 11, 2))['status'] == 'unavailable'


@pytest.mark.parametrize('rows', [
    [{'f12': '600000', 'f13': 1, 'f14': 'ST样本'}, {'f12': '600000', 'f13': 1, 'f14': 'ST样本'}],
    [{'f12': '600000', 'f13': 0, 'f14': 'ST样本'}],
    [{'f12': 'bad', 'f13': 1, 'f14': 'ST样本'}],
])
def test_risk_warning_rejects_duplicate_and_invalid_identity(tmp_path, rows):
    from radar.stocks.risk_warning import load_risk_warning_source

    snapshot(tmp_path, rows=rows)
    assert load_risk_warning_source(tmp_path, '600000', date(2026, 11, 3))['status'] == 'unavailable'


def test_risk_warning_rejects_tampering(tmp_path):
    from radar.stocks.risk_warning import load_risk_warning_source

    root = snapshot(tmp_path)
    (root / 'page.json').write_bytes(b'{}')
    assert load_risk_warning_source(tmp_path, '600000', date(2026, 11, 3))['status'] == 'unavailable'


def test_collect_saves_raw_pages_and_failure_disables_old_snapshot(tmp_path):
    from datetime import datetime

    from radar.stocks.admission import _BJ
    from radar.stocks.risk_warning import collect_risk_warning, load_risk_warning_source

    today = datetime.now(_BJ).date()
    raw = json.dumps({'rc': 0, 'data': {'total': 1, 'diff': [{'f12': '600000', 'f13': 1, 'f14': 'ST样本'}]}}).encode()
    result = collect_risk_warning(tmp_path, fetch=lambda page: (200, raw), sleep=lambda _: None)
    assert result['status'] == 'complete'
    assert load_risk_warning_source(tmp_path, '600000', today)['observation'] == 'observed'
    failed = collect_risk_warning(tmp_path, fetch=lambda page: (429, b''), sleep=lambda _: None)
    assert failed['status'] == 'failed'
    assert load_risk_warning_source(tmp_path, '600000', today)['status'] == 'unavailable'


def test_risk_warning_command_uses_explicit_state_without_formal_write(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import stock_robot.stock_risk_warning as module
    from stock_robot.stock_risk_warning import risk_warning_update

    seen = []
    monkeypatch.setattr(module, 'collect_risk_warning', lambda path: seen.append(path) or {'status': 'complete'})
    result = CliRunner().invoke(risk_warning_update, ['--state-dir', str(tmp_path)])
    assert result.exit_code == 0
    assert seen == [tmp_path]


def test_risk_warning_rejects_provider_error_and_truncated_pages(tmp_path):
    from radar.stocks.risk_warning import load_risk_warning_source

    root = snapshot(tmp_path)
    manifest = json.loads((root / 'manifest.json').read_bytes())
    for payload in ({'rc': 1, 'data': {'total': 1, 'diff': [{'f12': '600000', 'f13': 1, 'f14': 'ST样本'}]}},
                    {'rc': 0, 'data': {'total': 101, 'diff': [{'f12': '600000', 'f13': 1, 'f14': 'ST样本'}]}}):
        raw = json.dumps(payload).encode()
        (root / 'page.json').write_bytes(raw)
        manifest['pages'][0].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        assert load_risk_warning_source(tmp_path, '600000', date(2026, 11, 3))['status'] == 'unavailable'


def test_risk_warning_rejects_deep_market_identity_mismatch(tmp_path):
    from radar.stocks.risk_warning import load_risk_warning_source

    snapshot(tmp_path, rows=[{'f12': '000001', 'f13': 1, 'f14': 'ST样本'}])
    assert load_risk_warning_source(tmp_path, '600000', date(2026, 11, 3))['status'] == 'unavailable'


def test_collect_rejects_concurrent_update_without_overwriting_snapshot(tmp_path):
    from filelock import FileLock

    from radar.stocks.risk_warning import collect_risk_warning

    root = snapshot(tmp_path)
    before = (root / 'manifest.json').read_bytes()
    with FileLock(str(root / '.collect.lock')):
        result = collect_risk_warning(tmp_path, fetch=lambda page: pytest.fail('不能发起并发请求'), sleep=lambda _: None)
    assert result['status'] == 'failed'
    assert (root / 'manifest.json').read_bytes() == before


def test_locked_source_and_deep_invalid_json_are_unavailable(tmp_path):
    from filelock import FileLock

    from radar.stocks.risk_warning import load_risk_warning_source

    root = snapshot(tmp_path)
    with FileLock(str(root / '.collect.lock')):
        assert load_risk_warning_source(tmp_path, '600000', date(2026, 11, 3))['status'] == 'unavailable'
    raw = b'[' * 2000 + b']' * 2000
    (root / 'page.json').write_bytes(raw)
    manifest = json.loads((root / 'manifest.json').read_bytes())
    manifest['pages'][0].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
    assert load_risk_warning_source(tmp_path, '600000', date(2026, 11, 3))['status'] == 'unavailable'


def test_stale_lock_file_does_not_block_recovery(tmp_path):
    from radar.stocks.risk_warning import collect_risk_warning

    root = tmp_path / 'stock-risk-warning'
    root.mkdir()
    (root / '.collect.lock').write_text('stale', encoding='utf8')
    raw = json.dumps({'rc': 0, 'data': {'total': 1, 'diff': [
        {'f12': '600000', 'f13': 1, 'f14': 'ST样本'}]}}).encode()
    assert collect_risk_warning(tmp_path, fetch=lambda page: (200, raw), sleep=lambda _: None)['status'] == 'complete'


def test_process_termination_releases_collection_lock(tmp_path):
    import subprocess
    import sys

    from radar.stocks.risk_warning import collect_risk_warning

    root = tmp_path / 'stock-risk-warning'
    root.mkdir()
    code = ('from filelock import FileLock; import sys,time; '
            'lock=FileLock(sys.argv[1]); lock.acquire(); print("ready",flush=True); time.sleep(30)')
    process = subprocess.Popen([sys.executable, '-c', code, str(root / '.collect.lock')],
                               stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout is not None and process.stdout.readline().strip() == 'ready'
    finally:
        process.kill()
        process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
    raw = json.dumps({'rc': 0, 'data': {'total': 1, 'diff': [
        {'f12': '600000', 'f13': 1, 'f14': 'ST样本'}]}}).encode()
    assert collect_risk_warning(tmp_path, fetch=lambda page: (200, raw), sleep=lambda _: None)['status'] == 'complete'


def test_duplicate_json_keys_are_not_accepted(tmp_path):
    from radar.stocks.risk_warning import load_risk_warning_source

    root = snapshot(tmp_path)
    raw = b'{"rc":0,"rc":1,"data":{"total":1,"diff":[{"f12":"600000","f13":1,"f14":"ST"}]}}'
    (root / 'page.json').write_bytes(raw)
    manifest = json.loads((root / 'manifest.json').read_bytes())
    manifest['pages'][0].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
    assert load_risk_warning_source(tmp_path, '600000', date(2026, 11, 3))['status'] == 'unavailable'


def test_concurrent_read_waits_for_active_lock_and_keeps_source(tmp_path):
    import threading
    import time

    from filelock import FileLock

    from radar.stocks.risk_warning import load_risk_warning_source

    root = snapshot(tmp_path)
    ready = threading.Event()
    def hold():
        with FileLock(str(root / '.collect.lock')):
            ready.set()
            time.sleep(0.25)
    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert ready.wait(2)
        source = load_risk_warning_source(tmp_path, '600000', date(2026, 11, 3))
        assert source['status'] == 'current' and source['observation'] == 'observed'
    finally:
        thread.join(timeout=5)

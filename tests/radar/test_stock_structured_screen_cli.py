"""财务初筛从固定清单的已保存产物读取，不再请求数据或改成员。"""

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from stock_robot.stock_structured_financial import (
    run_structured_batch,
    screen_saved_batch,
    structured_screen_command,
)


def _saved(tmp_path):
    resource = Path(__file__).parents[1] / 'resources/radar/eastmoney-structured-financial-samples.json'
    sample = json.loads(resource.read_text(encoding='utf-8'))[0]

    def collect(_symbol, _cache_dir, **_kwargs):
        return sample

    root = tmp_path / 'collection'
    report = run_structured_batch([{'symbol': '000100', 'name': 'TCL科技'}], root, date(2026, 10, 17), collector=collect)
    return root, report


def test_reads_verified_artifact_without_network_and_reports_unattempted(tmp_path, monkeypatch):
    root, report = _saved(tmp_path)
    monkeypatch.setattr('radar.stocks.structured_collect._fetch', lambda *_: pytest.fail('离线初筛禁止网络'))
    result = screen_saved_batch(root/'summary.json')
    assert result['summary']['total'] == 1
    assert result['summary']['numeric_passed'] == 1
    assert result['members_published'] == 0
    assert result['items'][0]['symbol'] == report['items'][0]['symbol']


def test_corrupt_artifact_becomes_data_gap_not_eliminated_company(tmp_path):
    root, report = _saved(tmp_path)
    Path(report['items'][0]['artifact']['path']).write_bytes(b'{}')
    result = screen_saved_batch(root/'summary.json')
    assert result['summary']['numeric_passed'] == 0
    assert result['summary']['pending_verification'] == 1
    assert result['items'][0]['financial_numeric_status'] == 'pending_verification'


def test_recomputes_from_collection_not_supplied_normalized_verdict(tmp_path):
    root, report = _saved(tmp_path)
    ref = report['items'][0]['artifact']
    payload = json.loads(Path(ref['path']).read_bytes())
    payload['normalized']['financial_numeric_status'] = 'failed'
    raw = json.dumps(payload, ensure_ascii=False).encode()
    sha = hashlib.sha256(raw).hexdigest()
    path = root/'artifacts'/f'{sha}.json'
    path.write_bytes(raw)
    ref.update(path=str(path.resolve()), sha256=sha, bytes=len(raw))
    (root/'summary.json').write_text(json.dumps(report), encoding='utf-8')
    result = screen_saved_batch(root/'summary.json')
    assert result['summary']['numeric_passed'] == 1


def test_unattempted_companies_appear_separately(tmp_path):
    root, report = _saved(tmp_path)
    report['items'] = []
    (root/'summary.json').write_text(json.dumps(report), encoding='utf-8')
    result = screen_saved_batch(root/'summary.json')
    assert result['summary']['not_attempted'] == 1
    assert result['summary']['numeric_passed'] == 0


def test_summary_binding_mismatch_is_rejected(tmp_path):
    root, report = _saved(tmp_path)
    report['roster_sha256'] = '0'*64
    (root/'summary.json').write_text(json.dumps(report), encoding='utf-8')
    with pytest.raises(ValueError, match='绑定'):
        screen_saved_batch(root/'summary.json')


def test_cli_output_cannot_overwrite_running_collection(tmp_path):
    root, _ = _saved(tmp_path)
    original = (root/'summary.json').read_bytes()
    result = CliRunner().invoke(structured_screen_command, ['--input', str(root/'summary.json'), '--output', str(root/'summary.json')])
    assert result.exit_code != 0
    assert (root/'summary.json').read_bytes() == original

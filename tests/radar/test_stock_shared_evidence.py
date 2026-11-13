"""共享证据：采集前归档与评价文件身份。"""
from __future__ import annotations

import json
from datetime import date

from radar.stocks.shared_evidence import archive_governance, evaluation_identity


def test_archive_keeps_last_verified_snapshot(tmp_path):
    root = tmp_path / 'stock-exchange-governance'
    root.mkdir(parents=True)
    (root / 'manifest.json').write_text(json.dumps({'status': 'complete', 'as_of': '2026-11-07'}), encoding='utf-8')
    (root / 'index.json').write_text('{}', encoding='utf-8')
    (root / 'sse-001.json').write_text('[]', encoding='utf-8')
    target = archive_governance(tmp_path)
    assert target is not None
    archived = root / 'archive' / '2026-11-07'
    assert (archived / 'manifest.json').is_file() and (archived / 'sse-001.json').is_file()
    assert archive_governance(tmp_path) == target


def test_archive_skips_incomplete_or_missing(tmp_path):
    assert archive_governance(tmp_path) is None
    root = tmp_path / 'stock-exchange-governance'
    root.mkdir(parents=True)
    (root / 'manifest.json').write_text(json.dumps({'status': 'failed', 'as_of': '2026-11-07'}), encoding='utf-8')
    assert archive_governance(tmp_path) is None


def test_evaluation_identity_tracks_publish_date():
    current = evaluation_identity(date(2026, 11, 13))
    assert current['current'] is True
    assert current['published_on'] == '2025-10-31'
    assert current['file_sha256']
    assert evaluation_identity(date(2025, 1, 1))['current'] is False

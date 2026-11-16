"""指数初筛命令的可复用入口与文件写入边界。"""

import json
from datetime import date

import pytest
from click.testing import CliRunner

from stock_robot import stock_index_scope
from stock_robot.stock_index_scope import index_scope_command


def test_command_exports_scope_without_research_or_member_database(tmp_path, monkeypatch):
    seen = []

    def collect(root, **kwargs):
        seen.append((root, kwargs))
        return {'as_of': '2026-10-16', 'status': 'partial', 'pools': {},
                'formal_screening_enabled': False, 'members_published': 0}

    monkeypatch.setattr(stock_index_scope, 'collect_index_scope', collect)
    output = tmp_path/'scope.json'
    result = CliRunner().invoke(index_scope_command, ['--as-of', '2026-10-16',
        '--cache-dir', str(tmp_path/'cache'), '--output', str(output), '--refresh'])
    assert result.exit_code == 0, result.output
    assert seen[0][1] == {'as_of': date(2026, 10, 16), 'refresh': True}
    assert json.loads(output.read_text(encoding='utf-8'))['status'] == 'partial'
    assert not list(tmp_path.rglob('*.db'))


@pytest.mark.parametrize('location', ['same', 'inside'])
def test_cache_output_overlap_rejected_before_network(tmp_path, monkeypatch, location):
    cache = tmp_path/'cache'
    monkeypatch.setattr(stock_index_scope, 'collect_index_scope', lambda *a, **k: pytest.fail('不能采集'))
    result = CliRunner().invoke(index_scope_command, ['--as-of', '2026-10-16', '--cache-dir', str(cache),
        '--output', str(cache if location == 'same' else cache/'output.json')])
    assert result.exit_code != 0 and not cache.exists()


@pytest.mark.parametrize('cutoff', ['bad-date', '9999-12-31'])
def test_invalid_date_rejected_before_network(tmp_path, monkeypatch, cutoff):
    monkeypatch.setattr(stock_index_scope, 'collect_index_scope', lambda *a, **k: pytest.fail('不能采集'))
    result = CliRunner().invoke(index_scope_command, ['--as-of', cutoff, '--cache-dir', str(tmp_path/'cache'),
        '--output', str(tmp_path/'scope.json')])
    assert result.exit_code != 0 and not (tmp_path/'scope.json').exists()


def test_command_registered():
    from stock_robot.cli import main

    result = CliRunner().invoke(main, ['radar', 'stock-index-discover', '--help'])
    assert result.exit_code == 0, result.output

"""股本研究命令验证输入保护、日期边界与无数据库副作用。"""
import json
from datetime import date

import pytest
from click.testing import CliRunner

from stock_robot import stock_capital_history
from stock_robot.stock_capital_history import capital_history_command

AS_OF = date(2026, 10, 15)


def arguments(tmp_path):
    source = tmp_path/'companies.json'
    source.write_text('[{"symbol":"600900"},{"symbol":"601318"}]', encoding='utf-8')
    cache, output = tmp_path/'cache', tmp_path/'output.json'
    return source, cache, output


def invoke(source, cache, output, *extra):
    return CliRunner().invoke(capital_history_command, ['--input', str(source), '--as-of', AS_OF.isoformat(),
        '--cache-dir', str(cache), '--output', str(output), *extra])


def test_export_passes_explicit_range_and_retains_trace_without_database_changes(tmp_path, monkeypatch):
    source, cache, output = arguments(tmp_path)
    sentinel = tmp_path/'research.db'
    sentinel.write_bytes(b'no database operation')
    seen = []
    def collect(companies, directory, **kwargs):
        seen.append((companies, directory, kwargs))
        return {'status':'partial', 'items':[], 'unit_verified':False,
                'all_share_classes_verified':False, 'formal_screening_enabled':False, 'members_published':0}
    monkeypatch.setattr(stock_capital_history, 'collect_capital_history', collect)
    result = invoke(source, cache, output, '--start-date', '2000-01-01', '--refresh')
    assert result.exit_code == 0, result.output
    assert seen == [([{'symbol':'600900'}, {'symbol':'601318'}], cache,
        {'as_of':AS_OF, 'start_date':date(2000,1,1), 'refresh':True})]
    value = json.loads(output.read_text(encoding='utf-8'))
    assert value['input_source']['sha256']
    assert value['unit_verified'] is False and value['members_published'] == 0
    assert sentinel.read_bytes() == b'no database operation'


@pytest.mark.parametrize('target', ['same', 'input_in_cache', 'output_in_cache'])
def test_rejects_path_overlap_before_requests_or_writes(tmp_path, monkeypatch, target):
    source, cache, output = arguments(tmp_path)
    if target == 'same':
        output = source
    elif target == 'input_in_cache':
        cache = source.parent
    else:
        output = cache/'output.json'
    before = source.read_bytes()
    monkeypatch.setattr(stock_capital_history, 'collect_capital_history', lambda *a,**k:pytest.fail('不得采集'))
    result = invoke(source, cache, output)
    assert result.exit_code != 0
    assert source.read_bytes() == before
    assert not output.exists() or output == source


@pytest.mark.parametrize('payload', [None, {}, [], {'companies':[]}, ['600900'], [{'symbol':'bad'}],
    [{'symbol':'600900'}, {'symbol':'600900'}]])
def test_bad_companies_rejected_before_cache_creation(tmp_path, monkeypatch, payload):
    source, cache, output = arguments(tmp_path)
    source.write_text(json.dumps(payload), encoding='utf-8')
    monkeypatch.setattr(stock_capital_history, 'collect_capital_history', lambda *a,**k:pytest.fail('不得采集'))
    result = invoke(source, cache, output)
    assert result.exit_code != 0
    assert not cache.exists() and not output.exists()


@pytest.mark.parametrize('extra', [('--as-of','2999-01-01'), ('--start-date','2026-10-16'),
    ('--start-date','2026-02-30')])
def test_bad_range_preflight_does_not_collect(tmp_path, monkeypatch, extra):
    source, cache, output = arguments(tmp_path)
    monkeypatch.setattr(stock_capital_history, 'collect_capital_history', lambda *a,**k:pytest.fail('不得采集'))
    result = invoke(source, cache, output, *extra)
    assert result.exit_code != 0
    assert not cache.exists() and not output.exists()


def test_default_start_is_request_boundary_not_listing_fact(tmp_path, monkeypatch):
    source, cache, output = arguments(tmp_path)
    seen = []
    def collect(*args, **kwargs):
        seen.append(kwargs['start_date'])
        return {'status':'complete', 'unit_verified':False, 'financial_verified':False}
    monkeypatch.setattr(stock_capital_history, 'collect_capital_history', collect)
    assert invoke(source, cache, output).exit_code == 0
    assert seen == [date(1990,1,1)]


def test_command_registered():
    from stock_robot.cli import main
    result = CliRunner().invoke(main, ['radar', 'stock-capital-history', '--help'])
    assert result.exit_code == 0, result.output

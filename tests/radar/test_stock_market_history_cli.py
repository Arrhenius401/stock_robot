"""股票原始日线命令的输入保护与研究输出，使用合成请求。"""
import json
from datetime import date, timedelta

import pytest
from click.testing import CliRunner

from stock_robot import stock_market_history
from stock_robot.stock_market_history import market_history_command

AS_OF = date(2026, 10, 15)


def inputs(tmp_path):
    source = tmp_path / 'companies.json'
    source.write_text('[{"symbol":"600900","name":"合成公司"}]', encoding='utf-8')
    window = tmp_path / 'window.json'
    window.write_text(json.dumps([(AS_OF-timedelta(days=59-i)).isoformat() for i in range(60)]), encoding='utf-8')
    return source, window


def args(source, window, cache, output):
    return ['--input', str(source), '--market-window', str(window), '--as-of', AS_OF.isoformat(),
            '--cache-dir', str(cache), '--output', str(output)]


def test_command_exports_research_trace_without_touching_database(tmp_path, monkeypatch):
    source, window = inputs(tmp_path)
    cache, output = tmp_path/'cache', tmp_path/'result.json'
    database = tmp_path/'research.db'
    database.write_bytes(b'untouched synthetic database sentinel')
    seen = []
    def collect(companies, days, root, **kwargs):
        seen.append((companies, days, root, kwargs))
        return {'status':'partial', 'items':[{'symbol':'600900', 'status':'source_error'}],
                'research_only':True, 'formal_screening_enabled':False, 'financial_verified':False}
    monkeypatch.setattr(stock_market_history, 'collect_stock_history', collect)
    result = CliRunner().invoke(market_history_command, args(source, window, cache, output)+['--refresh'])
    assert result.exit_code == 0, result.output
    assert len(seen) == 1 and len(seen[0][1]) == 60
    assert seen[0][3]['as_of'] == AS_OF and seen[0][3]['refresh'] is True
    exported = json.loads(output.read_text(encoding='utf-8'))
    assert exported['status'] == 'partial'
    assert exported['input_source']['sha256'] and exported['market_window_source']['sha256']
    assert exported['formal_screening_enabled'] is False
    assert database.read_bytes() == b'untouched synthetic database sentinel'


@pytest.mark.parametrize('target', ['input','window','cache_output','cache_input','cache_window'])
def test_path_rejection_precedes_requests_and_output_changes(tmp_path, monkeypatch, target):
    source, window = inputs(tmp_path)
    cache, output = tmp_path/'cache', tmp_path/'result.json'
    if target == 'input':
        output = source
    elif target == 'window':
        output = window
    elif target == 'cache_output':
        output = cache/'result.json'
    elif target == 'cache_input':
        cache = source.parent
    else:
        nested = tmp_path/'nested'
        nested.mkdir()
        new_source = nested/'companies.json'
        new_source.write_bytes(source.read_bytes())
        source = new_source
        cache = window.parent
    before = source.read_bytes(), window.read_bytes()
    monkeypatch.setattr(stock_market_history, 'collect_stock_history', lambda *a,**k:pytest.fail('路径拒绝后不得请求'))
    result = CliRunner().invoke(market_history_command, args(source, window, cache, output))
    assert result.exit_code != 0
    assert before == (source.read_bytes(), window.read_bytes())
    if target not in {'input','window'}:
        assert not output.exists()


@pytest.mark.parametrize('payload', [None, {}, {'companies':'bad'}, ['bad']])
def test_invalid_companies_do_not_create_cache(tmp_path, monkeypatch, payload):
    source, window = inputs(tmp_path)
    source.write_text(json.dumps(payload), encoding='utf-8')
    cache, output = tmp_path/'cache', tmp_path/'result.json'
    monkeypatch.setattr(stock_market_history, 'collect_stock_history', lambda *a,**k:pytest.fail('无效公司不得请求'))
    result = CliRunner().invoke(market_history_command, args(source, window, cache, output))
    assert result.exit_code != 0
    assert not cache.exists() and not output.exists()


def test_market_history_command_registered():
    from stock_robot.cli import main
    result = CliRunner().invoke(main, ['radar', 'stock-market-history', '--help'])
    assert result.exit_code == 0, result.output

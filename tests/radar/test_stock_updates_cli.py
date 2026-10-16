"""股票手动任务命令通过真实SQLite及明确采集替身往返。"""

import json
from datetime import datetime

import pytest
from click.testing import CliRunner

from radar.stocks.admission import _BJ
from radar.stocks.research_store import StockResearchStore
from radar.stocks.update_runner import StockUpdateRunner
from stock_robot import stock_updates


def invoke(command, *args):
    from stock_robot.cli import main
    return CliRunner().invoke(main, ['radar', command, *map(str, args)])


@pytest.fixture
def paths(tmp_path):
    db = tmp_path/'research.db'
    StockResearchStore(db)
    today = datetime.now(_BJ).date().isoformat()
    source = tmp_path/'request.json'
    source.write_text(json.dumps({'kind': 'discover_candidates', 'pool_ids': ['dividend_value', 'growth_trend'],
                                  'target_date': today, 'rule_version': 'unconfigured'}), encoding='utf-8')
    return db, source, today


def test_enqueue_deduplicates_then_executes_real_import_and_lists(tmp_path, paths, monkeypatch):
    db, source, today = paths
    calls = []
    def collect(*args, **kwargs):
        calls.append(kwargs)
        return {'as_of': today, 'companies': [{'symbol': '600900', 'name': '合成公司'}], 'status': 'complete',
                'scope_complete': True, 'formal_screening_enabled': False}
    def runner(store, cache, artifacts, **kwargs):
        return StockUpdateRunner(store, cache, artifacts, roster_collector=collect, **kwargs)
    monkeypatch.setattr(stock_updates, 'StockUpdateRunner', runner)
    args = ['--db', db, '--input', source]
    first = invoke('stock-update-enqueue', *args)
    assert first.exit_code == 0, first.output
    again = invoke('stock-update-enqueue', *args)
    assert again.exit_code == 0, again.output
    assert json.loads(first.output)['run_id'] == json.loads(again.output)['run_id']
    finished = invoke('stock-update-run-one', '--db', db, '--cache-dir', tmp_path/'cache', '--artifact-dir', tmp_path/'artifacts')
    assert finished.exit_code == 0, finished.output
    assert json.loads(finished.output)['status'] == 'completed' and len(calls) == 1
    assert StockResearchStore(db).summary()['companies'] == 1
    listed = invoke('stock-update-list', '--db', db)
    assert listed.exit_code == 0, listed.output
    value = json.loads(listed.output)
    assert value['total'] == 1 and value['automatic_enabled'] is False
    empty = invoke('stock-update-run-one', '--db', db)
    assert empty.exit_code == 0 and '没有待执行' in empty.output


@pytest.mark.parametrize('mutation', ['future', 'extra', 'empty_members'])
def test_invalid_or_empty_scope_never_queues(paths, mutation):
    db, source, _today = paths
    request = json.loads(source.read_text(encoding='utf-8'))
    if mutation == 'future':
        request['target_date'] = '2999-01-01'
    elif mutation == 'extra':
        request['eligible'] = True
    else:
        request['kind'] = 'refresh_members'
    source.write_text(json.dumps(request), encoding='utf-8')
    result = invoke('stock-update-enqueue', '--db', db, '--input', source)
    assert result.exit_code != 0
    listed = invoke('stock-update-list', '--db', db)
    assert listed.exit_code == 0 and json.loads(listed.output)['total'] == 0


@pytest.mark.parametrize('target', ['input', 'database', 'database_wal'])
def test_output_protection_precedes_enqueue(paths, target):
    db, source, _today = paths
    output = source if target == 'input' else (db if target == 'database' else str(db)+'-wal')
    before = source.read_bytes()
    result = invoke('stock-update-enqueue', '--db', db, '--input', source, '--output', output)
    assert result.exit_code != 0
    assert source.read_bytes() == before and StockResearchStore(db).summary()['companies'] == 0


def test_runner_rejects_database_parent_storage_without_claim(paths):
    db, source, _today = paths
    assert invoke('stock-update-enqueue', '--db', db, '--input', source).exit_code == 0
    result = invoke('stock-update-run-one', '--db', db, '--cache-dir', db.parent)
    assert result.exit_code != 0 and 'SQLite' in result.output
    listed = invoke('stock-update-list', '--db', db)
    assert json.loads(listed.output)['items'][0]['status'] == 'queued'


@pytest.mark.parametrize('name', ['stock-update-enqueue', 'stock-update-list', 'stock-update-run-one'])
def test_commands_registered(name):
    assert invoke(name, '--help').exit_code == 0

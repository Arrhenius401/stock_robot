"""新财务任务直接驱动成长；失败的新任务不能回退旧登记。"""
import hashlib
import json

import pytest

from radar.stocks.growth_source import load_growth_source
from radar.stocks.research_store import StockRevisionConflict
from radar.stocks.update_runner import _artifact
from stock_robot.stock_admission import evaluate_store
from tests.radar.test_stock_financial_source import DAY, SYMBOL, enqueue, environment
from tests.radar.test_stock_growth_research import rows
from tests.radar.test_stock_growth_source import prepare


def save_main_task(tmp_path, tasks):
    run = enqueue(tasks)
    claimed = tasks.claim('worker')
    assert claimed is not None
    root = tmp_path / 'stock-updates/cache/structured-financial'
    root.mkdir(parents=True)
    data = rows()
    raw = json.dumps({'success': True, 'result': {'pages': 1, 'count': len(data), 'data': data}}).encode()
    digest = hashlib.sha256(raw).hexdigest()
    path = root / (digest + '.raw')
    path.write_bytes(raw)
    collection = {'symbol': SYMBOL, 'as_of': DAY.isoformat(), 'sources': {'main': {
        'raw_artifact': str(path.resolve()), 'content_sha256': digest, 'bytes': len(raw),
        'fetched_at': '2026-10-18T12:00:00+08:00',
        'source_url': 'https://datacenter.eastmoney.com/securities/api/data/get',
        'request_params': {'type': 'RPT_F10_FINANCE_MAINFINADATA', 'filter': '(SECUCODE="600900.SH")'}}}}
    ref = _artifact(tmp_path / 'stock-updates/artifacts', {'symbol': SYMBOL, 'as_of': DAY.isoformat(), 'collection': collection})
    tasks.record_item(run['run_id'], 'worker', {'scope': 'financial:' + SYMBOL, 'status': 'partial',
        'summary': '其他财务表待补', 'artifact': ref}, lease_token=claimed['lease_token'])
    tasks.finish(run['run_id'], 'worker', lease_token=claimed['lease_token'], status='partial')
    return run, path


def test_task_main_can_compute_growth_with_other_financial_tables_missing(tmp_path):
    _, _, tasks, _ = environment(tmp_path)
    run, raw = save_main_task(tmp_path, tasks)
    source = load_growth_source(tmp_path, SYMBOL, DAY)
    assert source['run_id'] == run['run_id'] and source['status'] == 'current'
    assert source['research']['paths']['sustained_growth'] == 'met_reference'
    assert str(tmp_path) not in json.dumps(source)
    before = source
    raw.write_bytes(b'{}')
    source = load_growth_source(tmp_path, SYMBOL, DAY)
    assert source != before and source['status'] == 'unavailable'


@pytest.mark.parametrize('damage', ['not_attempted', 'bad_scope', 'missing_raw', 'version'])
def test_applicable_new_task_blocks_old_registry(tmp_path, damage):
    prepare(tmp_path)
    _, _, tasks, _ = environment(tmp_path)
    if damage == 'missing_raw':
        _, raw = save_main_task(tmp_path, tasks)
        raw.unlink()
    else:
        run = enqueue(tasks, version='unknown') if damage == 'version' else enqueue(tasks)
        if damage == 'bad_scope':
            with tasks.research._write() as connection:
                connection.execute('UPDATE stocks_update_runs SET input_snapshot=? WHERE run_id=?', ('{}', run['run_id']))
    source = load_growth_source(tmp_path, SYMBOL, DAY)
    assert source['status'] == 'unavailable' and source['research'] is None
    assert source.get('run_id')


def test_new_cross_pool_task_invalidates_archived_growth_without_recollecting(tmp_path):
    research, archive, tasks, _ = environment(tmp_path)
    run, _ = save_main_task(tmp_path, tasks)
    source = load_growth_source(tmp_path, SYMBOL, DAY)
    report = evaluate_store(research, DAY, symbol=SYMBOL, include_styles=True, growth_sources={SYMBOL: source})
    inputs = archive.capture_inputs(symbol=SYMBOL)
    inputs['companies'][0].update(growth_source=source, growth_as_of=DAY.isoformat())
    saved = archive.save_evaluation(report, inputs)
    assert source['run_id'] == run['run_id']
    enqueue(tasks, pool='growth_trend')
    with pytest.raises(StockRevisionConflict):
        archive.assert_current(saved['evaluation_id'])


def test_custom_database_name_is_used_for_selection_and_archive(tmp_path):
    research, archive, tasks, _ = environment(tmp_path)
    run, _ = save_main_task(tmp_path, tasks)
    custom = tmp_path / 'custom-research.db'
    with research._connect() as connection:
        import sqlite3
        with sqlite3.connect(custom) as target:
            connection.backup(target)
    from radar.stocks.evaluation_store import StockEvaluationStore
    from radar.stocks.research_store import StockResearchStore

    source = load_growth_source(tmp_path, SYMBOL, DAY, db=custom)
    assert source['run_id'] == run['run_id']
    custom_store = StockResearchStore(custom)
    archive = StockEvaluationStore(custom_store)
    inputs = archive.capture_inputs(symbol=SYMBOL)
    inputs['companies'][0].update(growth_source=source, growth_as_of=DAY.isoformat())
    report = evaluate_store(custom_store, DAY, symbol=SYMBOL, include_styles=True, growth_sources={SYMBOL: source})
    saved = archive.save_evaluation(report, inputs)
    with custom_store._write() as connection:
        connection.execute('UPDATE stocks_update_runs SET rule_version=?', ('changed',))
    with pytest.raises(StockRevisionConflict):
        archive.assert_current(saved['evaluation_id'])

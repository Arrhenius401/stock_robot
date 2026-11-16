"""财务手动任务复用真实队列，采集替身只在网络边界注入。"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.financial_task import financial_results
from radar.stocks.membership_store import StockMembershipStore
from radar.stocks.pools import StockPoolRepository
from radar.stocks.research_store import StockResearchError, StockResearchStore
from radar.stocks.update_runner import StockUpdateRunner
from radar.stocks.update_runs import StockUpdateStore
from radar.stocks.web_queries import public_task

DAY = date(2026, 10, 16)


@pytest.fixture
def environment(tmp_path):
    clock = [datetime(2026, 10, 16, 8, tzinfo=UTC)]
    research = StockResearchStore(tmp_path/'research.db', clock=lambda: clock[0])
    pools = StockPoolRepository(Path(__file__).parents[2]/'config/radar_stock_pools')
    return StockUpdateStore(StockMembershipStore(StockEvaluationStore(research), pools)), research, clock


def snapshot():
    return {'kind': 'financial_review', 'pool_ids': ['dividend_value'], 'scope_complete': True,
            'scope_as_of': DAY.isoformat(), 'scope_sha256': 'a' * 64, 'source_run_id': 'source',
            'source_snapshots': [], 'companies': [{'symbol': '600900', 'name': '合成公司'}]}


def enqueue(store, value=None):
    return store.enqueue(kind='financial_review', pool_ids=['dividend_value'], target_date=DAY,
                         rule_version='financial-test', snapshot=value or snapshot())


def test_financial_snapshot_is_mandatory_and_frozen(environment):
    store, _, _ = environment
    with pytest.raises(StockResearchError):
        store.enqueue(kind='financial_review', pool_ids=['dividend_value'], target_date=DAY, rule_version='test')
    value = snapshot()
    first = enqueue(store, value)
    value['companies'].clear()
    assert store.get(first['run_id'])['input_snapshot']['companies'][0]['symbol'] == '600900'
    assert enqueue(store)['run_id'] == first['run_id']
    for changed in ({'scope_complete': False}, {'pool_ids': ['growth_trend']},
                    {'companies': []}, {'companies': [{'symbol': '../x', 'name': '坏代码'}]}):
        # 使用不同版本避免命中已排队任务。
        with pytest.raises(StockResearchError):
            store.enqueue(kind='financial_review', pool_ids=['dividend_value'], target_date=DAY,
                          rule_version=str(changed), snapshot={**snapshot(), **changed})


def test_financial_numeric_failure_is_successful_work_not_task_failure(tmp_path, environment):
    store, research, _ = environment
    sample = json.loads((Path(__file__).parents[1] / 'resources/radar/eastmoney-structured-financial-samples.json').read_text(encoding='utf-8'))[0]
    sample['as_of'] = DAY.isoformat()
    value = snapshot()
    value['companies'][0]['symbol'] = sample['symbol']
    # 把所有年度扣非利润设为负，证明确定不满足也属于成功复核。
    for row in sample['records']['profit']:
        row['DEDUCT_PARENT_NETPROFIT'] = '-10'
    for row in sample['records']['main']:
        row['KCFJCXSYJLR'] = '-10'
    enqueue(store, value)
    calls = []
    def collect(symbol, cache, **kwargs):
        calls.append((symbol, kwargs))
        return sample
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts', financial_collector=collect, max_workers=1).execute_one('worker')
    assert result is not None
    assert result['status'] == 'completed'
    assert calls == [(sample['symbol'], {'as_of': DAY})]
    safe = public_task(result)
    assert safe['progress']['attempted'] == 1
    assert safe['progress']['numeric_passed'] + safe['progress']['numeric_failed'] == 1
    assert 'path' not in json.dumps(safe)
    assert research.summary()['companies'] == 0
    assert store.memberships.list_members('dividend_value')['total'] == 0


def test_financial_source_failure_is_pending_and_429_stops_remaining(tmp_path, environment):
    store, _, _ = environment
    value = snapshot()
    value['companies'].append({'symbol': '600901', 'name': '第二家'})
    enqueue(store, value)
    calls = []
    def collect(symbol, cache, **kwargs):
        calls.append(symbol)
        return {'symbol': symbol, 'as_of': DAY.isoformat(), 'source_stopped': True, 'errors': ['429']}
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts', financial_collector=collect, max_workers=1).execute_one('worker')
    assert result is not None
    assert result['status'] == 'partial'
    assert calls == ['600900']
    assert public_task(result)['progress'] == {'total': 2, 'attempted': 1, 'not_attempted': 1,
        'numeric_passed': 0, 'numeric_failed': 0, 'pending_verification': 1}


def test_resume_keeps_frozen_scope_and_rejects_corrupt_artifact(tmp_path, environment):
    store, _, clock = environment
    task = enqueue(store)
    runner = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts',
        financial_collector=lambda symbol, cache, **kwargs: {'symbol': symbol, 'as_of': DAY.isoformat(), 'source_stopped': True})
    result = runner.execute_one('worker')
    assert result is not None
    resumed = store.resume_financial(task['run_id'], 'dividend_value')
    assert resumed['input_snapshot'] == task['input_snapshot']
    assert resumed['run_id'] == result['run_id'] and resumed['status'] == 'queued'
    assert financial_results(resumed, tmp_path/'artifacts', 1)['items'][0]['financial_numeric_status'] == 'pending_verification'
    reference = result['results'][0]['artifact']
    Path(reference['path']).write_bytes(b'corrupt')
    with pytest.raises(StockResearchError, match='长度|摘要'):
        financial_results(resumed, tmp_path/'artifacts', 1)
    # 终态恢复不改变输入；租约过期后的成功断点也必须验原公司与日期。
    claimed = store.claim('old')
    assert claimed is not None
    clock[0] += timedelta(seconds=91)
    assert store.recover_expired() == 1
    assert store.get(task['run_id'])['input_snapshot'] == task['input_snapshot']


def test_financial_result_pagination_preserves_not_attempted(environment, tmp_path):
    store, _, _ = environment
    value = snapshot()
    value['companies'] = [{'symbol': f'{600000 + index:06}', 'name': '未处理'} for index in range(16)]
    task = enqueue(store, value)
    first = financial_results(task, tmp_path/'artifacts', 1)
    second = financial_results(task, tmp_path/'artifacts', 2)
    assert len(first['items']) == 15 and len(second['items']) == 1
    assert second['items'][0]['financial_numeric_status'] == 'not_attempted'
    assert first['summary']['not_attempted'] == first['total'] == 16


def test_financial_source_diagnostics_are_safe_and_visible(tmp_path, environment):
    store, _, _ = environment
    enqueue(store)
    def collect(*args, **kwargs):
        raise OSError('C:/private/cache/secret.json 无法读取')
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts', financial_collector=collect, max_workers=1).execute_one('worker')
    assert result is not None
    page = financial_results(result, tmp_path/'artifacts', 1)
    item = page['items'][0]
    assert item['source_diagnostics']['code'] == 'source_error'
    assert item['source_diagnostics']['message']
    assert 'C:/private' not in json.dumps(item)


def test_financial_429_stop_diagnostic_is_exposed(tmp_path, environment):
    store, _, _ = environment
    enqueue(store)
    runner = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts',
        financial_collector=lambda symbol, cache, **kwargs: {'symbol': symbol, 'as_of': DAY.isoformat(), 'source_stopped': True, 'errors': ['429 Too Many Requests']})
    result = runner.execute_one('worker')
    assert result is not None
    page = financial_results(result, tmp_path/'artifacts', 1)
    assert page['items'][0]['source_diagnostics']['code'] == 'source_stopped'
    assert '429' in page['items'][0]['source_diagnostics']['message']
    assert public_task(result)['source_stopped'] is True

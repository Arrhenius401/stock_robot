"""真实任务仓储往返，网络由明确的适配器边界替代。"""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta

import pytest

from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.membership_store import StockMembershipStore
from radar.stocks.pools import StockPoolRepository
from radar.stocks.research_store import StockResearchError, StockResearchStore
from radar.stocks.update_runner import StockUpdateRunner
from radar.stocks.update_runs import StockUpdateStore

DAY = date(2026, 10, 16)


@pytest.fixture
def environment(tmp_path):
    clock = [datetime(2026, 10, 16, 8, tzinfo=UTC)]
    research = StockResearchStore(tmp_path/'research.db', clock=lambda: clock[0])
    pools = tmp_path/'pools'
    pools.mkdir()
    for index, pool_id in enumerate(('dividend_value', 'growth_trend')):
        (pools/f'{pool_id}.yaml').write_text(f'id: {pool_id}\nname: 合成池\ndescription: 合成测试\ndisplay_order: {index}\n', encoding='utf-8')
    memberships = StockMembershipStore(StockEvaluationStore(research), StockPoolRepository(pools))
    return StockUpdateStore(memberships), research, clock


def roster(*, complete=True):
    return {'as_of': DAY.isoformat(), 'companies': [{'symbol': '600900', 'name': '合成公司', 'admission_status': 'pending_verification'}],
            'status': 'complete' if complete else 'partial', 'scope_complete': complete,
            'items': [{'source': 'synthetic_official', 'status': 'downloaded' if complete else 'source_error'}],
            'formal_screening_enabled': False, 'financial_verified': False}


def enqueue(store):
    return store.enqueue(kind='discover_candidates', pool_ids=['dividend_value', 'growth_trend'], target_date=DAY, rule_version='unconfigured')


def test_discovery_archives_then_imports_unreviewed_shared_candidates(tmp_path, environment):
    store, research, _clock = environment
    task = enqueue(store)
    calls = []
    def collect(cache, **kwargs):
        calls.append((cache, kwargs))
        return roster()
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts', roster_collector=collect).execute_one('worker')
    assert result is not None and result['run_id'] == task['run_id'] and result['status'] == 'completed'
    assert len(calls) == 1 and calls[0][1] == {'as_of': DAY}
    assert research.summary()['companies'] == 1 and research.summary()['reviews'] == 0
    evidence = research.list_evidence('600900')
    assert all(row['review_status'] != 'confirmed' for row in evidence)
    reference = result['results'][-1]['artifact']
    raw = (tmp_path/'artifacts'/f"{reference['sha256']}.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == reference['sha256']
    assert json.loads(raw)['companies'][0]['symbol'] == '600900'
    assert store.memberships.list_members('dividend_value')['total'] == 0


def test_partial_source_retains_available_candidates_and_reports_partial(tmp_path, environment):
    store, research, _clock = environment
    enqueue(store)
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts', roster_collector=lambda *a, **k: roster(complete=False)).execute_one('worker')
    assert result is not None and result['status'] == 'partial'
    assert research.summary()['companies'] == 1
    assert result['results'][-1]['artifact']['scope_complete'] is False


def test_lease_expiry_during_fetch_prevents_candidate_import(tmp_path, environment):
    store, research, clock = environment
    enqueue(store)
    def collect(*args, **kwargs):
        clock[0] += timedelta(seconds=91)
        return roster()
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts', roster_collector=collect).execute_one('worker')
    assert result is not None and result['status'] == 'lease_lost'
    assert research.summary()['companies'] == 0 and research.summary()['batches'] == 0


def test_resume_after_import_result_saved_does_not_fetch_completed_scope(tmp_path, environment):
    store, _research, clock = environment
    task = enqueue(store)
    claimed = store.claim('previous-worker')
    assert claimed is not None
    raw = json.dumps(roster()).encode('utf-8')
    sha = hashlib.sha256(raw).hexdigest()
    root = tmp_path/'artifacts'
    root.mkdir()
    path = root/f'{sha}.json'
    path.write_bytes(raw)
    store.record_item(task['run_id'], 'previous-worker', {'scope': 'index_scope_v1', 'status': 'completed', 'summary': '已保存合成导入结果',
        'artifact': {'path': str(path), 'sha256': sha, 'bytes': len(raw)}}, lease_token=claimed['lease_token'])
    clock[0] += timedelta(seconds=91)
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts', roster_collector=lambda *a, **k: pytest.fail('不得重复抓取成功阶段')).execute_one('next-worker')
    assert result is not None and result['status'] == 'completed'


def test_all_sources_returning_errors_fail_without_empty_candidate_batch(tmp_path, environment):
    store, research, _clock = environment
    enqueue(store)
    value = {**roster(complete=False), 'companies': [], 'items': [{'source': 'sse_main', 'status': 'source_error', 'error': '合成错误'}]}
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts', roster_collector=lambda *a, **k: value).execute_one('worker')
    assert result is not None and result['status'] == 'failed'
    assert result['results'][0]['status'] == 'failed' and result['results'][0]['artifact']['sha256']
    assert research.summary()['companies'] == research.summary()['batches'] == 0


def test_legacy_full_market_checkpoint_does_not_skip_index_scope(tmp_path, environment):
    store, research, clock = environment
    task = enqueue(store)
    claimed = store.claim('previous-worker')
    assert claimed is not None
    root = tmp_path/'artifacts'
    root.mkdir()
    raw = b'{"legacy_full_market":true}'
    sha = hashlib.sha256(raw).hexdigest()
    path = root/f'{sha}.json'
    path.write_bytes(raw)
    store.record_item(task['run_id'], 'previous-worker', {
        'scope': 'official_roster', 'status': 'completed', 'summary': '旧版全市场底表',
        'artifact': {'path': str(path), 'sha256': sha, 'bytes': len(raw)}}, lease_token=claimed['lease_token'])
    clock[0] += timedelta(seconds=91)
    calls = []

    def collect(*args, **kwargs):
        calls.append(kwargs)
        return roster()

    result = StockUpdateRunner(store, tmp_path/'cache', root, roster_collector=collect).execute_one('next-worker')
    assert result is not None and result['status'] == 'completed'
    assert len(calls) == 1 and result['results'][-1]['scope'] == 'index_scope_v1'
    assert research.summary()['companies'] == 1 and research.summary()['reviews'] == 0


@pytest.mark.parametrize('damage', ['missing', 'hash', 'outside'])
def test_resume_rejects_unverifiable_completed_artifacts(tmp_path, environment, damage):
    store, research, clock = environment
    task = enqueue(store)
    claimed = store.claim('previous-worker')
    assert claimed is not None
    root = tmp_path/'artifacts'
    root.mkdir()
    raw = b'{"synthetic":"verified checkpoint"}'
    sha = hashlib.sha256(raw).hexdigest()
    path = root/f'{sha}.json'
    path.write_bytes(raw)
    if damage == 'missing':
        path.unlink()
    elif damage == 'hash':
        path.write_bytes(b'x'*len(raw))
    reference_path = tmp_path/'outside.json' if damage == 'outside' else path
    store.record_item(task['run_id'], 'previous-worker', {'scope': 'index_scope_v1', 'status': 'completed', 'summary': '合成断点',
        'artifact': {'path': str(reference_path), 'sha256': sha, 'bytes': len(raw)}}, lease_token=claimed['lease_token'])
    clock[0] += timedelta(seconds=91)
    result = StockUpdateRunner(store, tmp_path/'cache', root, roster_collector=lambda *a, **k: pytest.fail('损坏断点不得静默重抓覆盖')).execute_one('next-worker')
    assert result is not None and result['status'] == 'failed'
    assert research.summary()['companies'] == 0


def test_failed_source_becomes_visible_task_error(tmp_path, environment):
    store, research, _clock = environment
    enqueue(store)
    def collect(*args, **kwargs):
        raise ValueError('合成来源不可用')
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts', roster_collector=collect).execute_one('worker')
    assert result is not None and result['status'] == 'failed' and '合成来源不可用' in result['error']
    assert research.summary()['companies'] == 0


@pytest.mark.parametrize('location', ['database_parent', 'nested_cache'])
def test_invalid_storage_rejected_without_claim(tmp_path, environment, location):
    store, _research, _clock = environment
    task = enqueue(store)
    cache = tmp_path if location == 'database_parent' else tmp_path/'cache'
    artifacts = tmp_path/'artifacts' if location == 'database_parent' else cache/'artifacts'
    with pytest.raises(StockResearchError):
        StockUpdateRunner(store, cache, artifacts)
    assert store.get(task['run_id'])['status'] == 'queued'


def member_task(store, research):
    research.import_companies([{'symbol': '600900'}], source_sha256='a'*64, source_label='合成成员来源', data_as_of=DAY)
    with research._write() as connection:
        for pool_id in ('dividend_value', 'growth_trend'):
            connection.execute('INSERT INTO stocks_pool_revisions VALUES(?,0)', (pool_id,))
            connection.execute('INSERT INTO stocks_memberships VALUES(?,?,?,?,?,?,?,?)',
                (pool_id, '600900', 'active', None, 1, '合成成员', research._now().isoformat(), 1))
    return store.enqueue(kind='refresh_members', pool_ids=['dividend_value', 'growth_trend'], target_date=DAY, rule_version='unconfigured')


def test_member_refresh_deduplicates_symbols_and_does_not_claim_risk_review(tmp_path, environment):
    store, research, _clock = environment
    member_task(store, research)
    calls = []
    def history(companies, days, cache, **kwargs):
        calls.append(('market_history', companies, days))
        return {'as_of': DAY.isoformat(), 'status': 'complete', 'items': [
            {'status': 'downloaded', 'rows': [{'date': DAY.isoformat()}], 'window_complete': True}]}
    def capital(companies, cache, **kwargs):
        calls.append(('capital_history', companies))
        return {'as_of': DAY.isoformat(), 'status': 'complete', 'unit_verified': False, 'items': [
            {'status': 'downloaded', 'rows': [{'date': DAY.isoformat()}], 'complete': True}]}
    days = [DAY - timedelta(days=59-index) for index in range(60)]
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts', history_collector=history,
                               capital_collector=capital, market_window=days).execute_one('worker')
    assert result is not None and result['status'] == 'partial'
    assert [entry[0] for entry in calls] == ['market_history', 'capital_history']
    assert all(entry[1] == [{'symbol': '600900'}] for entry in calls)
    assert result['results'][-1]['scope'] == 'financial_and_risk_review'
    assert result['results'][-1]['status'] == 'not_configured'
    for pool_id in ('dividend_value', 'growth_trend'):
        member = store.memberships.get_member(pool_id, '600900')
        assert member is not None and member['status'] == 'active' and member['revision'] == 1


def test_missing_market_window_preserves_other_source_result(tmp_path, environment):
    store, research, _clock = environment
    member_task(store, research)
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts',
        history_collector=lambda *a, **k: pytest.fail('缺窗口不得请求行情'),
        capital_collector=lambda *a, **k: {'as_of': DAY.isoformat(), 'status': 'complete', 'items': [
            {'status': 'downloaded', 'rows': [{'date': DAY.isoformat()}], 'complete': True}]}).execute_one('worker')
    assert result is not None and result['status'] == 'partial'
    assert result['results'][0]['scope'] == 'market_history' and result['results'][0]['status'] == 'failed'
    assert result['results'][1]['scope'] == 'capital_history' and result['results'][1]['status'] == 'completed'


def test_all_member_sources_failed_does_not_look_partially_usable(tmp_path, environment):
    store, research, _clock = environment
    member_task(store, research)
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts',
        capital_collector=lambda *a, **k: {'as_of': DAY.isoformat(), 'status': 'partial', 'items': [{'status': 'source_error'}]}).execute_one('worker')
    assert result is not None and result['status'] == 'failed'
    assert all(item['status'] in {'failed', 'not_configured'} for item in result['results'])


def test_refresh_latest_failure_overrides_old_partial_after_lease_recovery(tmp_path, environment):
    store, research, clock = environment
    task = member_task(store, research)
    claimed = store.claim('previous-worker')
    assert claimed is not None
    store.record_item(task['run_id'], 'previous-worker', {'scope': 'market_history', 'status': 'partial', 'summary': '旧范围只有部分记录'}, lease_token=claimed['lease_token'])
    clock[0] += timedelta(seconds=91)
    result = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts',
        capital_collector=lambda *a, **k: {'as_of': DAY.isoformat(), 'status': 'partial', 'items': [{'status': 'source_error'}]}).execute_one('next-worker')
    assert result is not None and result['status'] == 'failed'
    assert result['results'][0]['status'] == 'partial'
    latest = {item['scope']: item['status'] for item in result['results']}
    assert latest == {'market_history': 'failed', 'capital_history': 'failed', 'financial_and_risk_review': 'not_configured'}


def test_corrupt_existing_artifact_is_preserved_and_not_imported_again(tmp_path, environment):
    store, research, _clock = environment
    enqueue(store)
    runner = StockUpdateRunner(store, tmp_path/'cache', tmp_path/'artifacts', roster_collector=lambda *a, **k: roster())
    first = runner.execute_one('worker')
    assert first is not None
    artifact_path = tmp_path/'artifacts'/f"{first['results'][0]['artifact']['sha256']}.json"
    artifact_path.write_bytes(b'corrupt')
    before = research.summary()
    enqueue(store)
    second = runner.execute_one('worker')
    assert second is not None and second['status'] == 'failed'
    assert artifact_path.read_bytes() == b'corrupt' and research.summary() == before

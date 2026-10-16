"""股票更新任务须与ETF更新和正式成员发布隔离。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier, Event
from typing import Any

import pytest

from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.membership_store import StockMembershipStore
from radar.stocks.pools import StockPoolRepository
from radar.stocks.research_store import (
    StockResearchError,
    StockResearchStore,
    StockRevisionConflict,
)
from radar.stocks.update_runs import StockUpdateStore

TODAY = date(2026, 10, 16)


def setup(tmp_path):
    clock = [datetime(2026, 10, 16, 1, tzinfo=UTC)]
    research = StockResearchStore(tmp_path/'research.db', clock=lambda: clock[0])
    research.import_companies([{'symbol': '600900', 'name': '合成公司'}], source_sha256='a'*64, source_label='合成', data_as_of=TODAY)
    directory = tmp_path/'pools'
    directory.mkdir()
    for index, pool_id in enumerate(('dividend_value', 'growth_trend')):
        (directory/f'{pool_id}.yaml').write_text(f'id: {pool_id}\nname: 合成池\ndescription: 合成\ndisplay_order: {index}\n', encoding='utf-8')
    memberships = StockMembershipStore(StockEvaluationStore(research), StockPoolRepository(directory))
    return StockUpdateStore(memberships), research, clock


def seed_member(store, research, pool_id='dividend_value'):
    now = research._now().isoformat()
    with research._write() as connection:
        connection.execute('INSERT INTO stocks_pool_revisions VALUES(?,0)', (pool_id,))
        connection.execute('INSERT INTO stocks_memberships VALUES(?,?,?,?,?,?,?,?)', (pool_id, '600900', 'active', 'a'*64, 1, '合成成员', now, 1))


def test_discovery_is_idempotent_and_never_publishes_member(tmp_path):
    store, _research, _clock = setup(tmp_path)
    first = store.enqueue(kind='discover_candidates', pool_ids=['growth_trend', 'dividend_value'], target_date=TODAY, rule_version='v1')
    again = store.enqueue(kind='discover_candidates', pool_ids=['dividend_value', 'growth_trend'], target_date=TODAY, rule_version='v1')
    assert first['run_id'] == again['run_id']
    assert first['input_snapshot']['members'] == []
    assert first['input_snapshot']['formal_screening_enabled'] is False


def test_member_refresh_requires_active_or_suspended_member_and_keeps_snapshot(tmp_path):
    store, research, _clock = setup(tmp_path)
    with pytest.raises(StockResearchError):
        store.enqueue(kind='refresh_members', pool_ids=['dividend_value'], target_date=TODAY, rule_version='v1')
    seed_member(store, research)
    queued = store.enqueue(kind='refresh_members', pool_ids=['dividend_value'], target_date=TODAY, rule_version='v1')
    assert queued['input_snapshot']['members'][0]['symbol'] == '600900'
    with research._write() as connection:
        connection.execute("UPDATE stocks_memberships SET status='exited' WHERE pool_id='dividend_value' AND symbol='600900'")
    assert store.get(queued['run_id'])['input_snapshot']['members'][0]['status'] == 'active'


def test_claim_record_finish_and_lease_protection(tmp_path):
    store, _research, _clock = setup(tmp_path)
    queued = store.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=TODAY, rule_version='v1')
    claimed = store.claim('worker-a')
    assert claimed is not None and claimed['run_id'] == queued['run_id'] and claimed['status'] == 'running'
    token = claimed['lease_token']
    store.record_item(claimed['run_id'], 'worker-a', {'scope': 'official_roster', 'status': 'not_configured', 'summary': '待接通'}, lease_token=token)
    with pytest.raises(StockRevisionConflict):
        store.finish(claimed['run_id'], 'worker-b', status='partial', lease_token=token)
    finished = store.finish(claimed['run_id'], 'worker-a', status='partial', error='适配器待接通', lease_token=token)
    assert finished['status'] == 'partial' and finished['results'][0]['status'] == 'not_configured'


def test_expired_lease_requeues_then_fails_on_third_attempt(tmp_path):
    store, _research, clock = setup(tmp_path)
    queued = store.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=TODAY, rule_version='v1')
    for expected_status in ('queued', 'queued', 'failed'):
        claimed = store.claim('worker')
        assert claimed is not None
        clock[0] += timedelta(seconds=91)
        assert store.recover_expired() == 1
        assert store.get(queued['run_id'])['status'] == expected_status


@pytest.mark.parametrize('kind', ['bad', '', None])
def test_bad_kind_is_rejected(tmp_path, kind):
    store, _research, _clock = setup(tmp_path)
    with pytest.raises(StockResearchError):
        store.enqueue(kind=kind, pool_ids=['dividend_value'], target_date=TODAY, rule_version='v1')


def test_expired_owner_and_same_worker_old_token_cannot_write(tmp_path):
    store, research, clock = setup(tmp_path)
    queued = store.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=TODAY, rule_version='v1')
    old = store.claim('worker')
    assert old is not None
    clock[0] += timedelta(seconds=90)
    with pytest.raises(StockRevisionConflict), research._write() as connection:
        store.assert_owner(connection, queued['run_id'], 'worker', old['lease_token'])
    store.recover_expired()
    new = store.claim('worker')
    assert new is not None and new['lease_token'] != old['lease_token']
    for operation in (
        lambda: store.heartbeat(queued['run_id'], 'worker', lease_token=old['lease_token']),
        lambda: store.record_item(queued['run_id'], 'worker', {'scope': 'x', 'status': 'completed', 'summary': '成功'}, lease_token=old['lease_token']),
        lambda: store.finish(queued['run_id'], 'worker', status='failed', lease_token=old['lease_token']),
    ):
        with pytest.raises(StockRevisionConflict):
            operation()


def test_global_claim_and_successful_scope_are_idempotent(tmp_path):
    store, _research, clock = setup(tmp_path)
    first = store.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=TODAY, rule_version='v1')
    store.enqueue(kind='discover_candidates', pool_ids=['growth_trend'], target_date=TODAY, rule_version='v1')
    run = store.claim('worker')
    assert run is not None and store.claim('other') is None
    item = {'scope': 'a', 'status': 'completed', 'summary': '成功', 'artifact': {'sha256': 'a'*64}}
    store.record_item(run['run_id'], 'worker', item, lease_token=run['lease_token'])
    store.record_item(run['run_id'], 'worker', item, lease_token=run['lease_token'])
    assert len(store.get(run['run_id'])['results']) == 1
    with pytest.raises(StockRevisionConflict):
        store.record_item(run['run_id'], 'worker', {**item, 'summary': '替换'}, lease_token=run['lease_token'])
    clock[0] += timedelta(seconds=91)
    store.recover_expired()
    resumed = store.claim('worker')
    assert resumed is not None
    if resumed['run_id'] == first['run_id']:
        assert resumed['completed_scopes'] == ['a']


@pytest.mark.parametrize('target', ['20261016', '2026-W42-5', '2026-10-17'])
def test_target_date_must_be_canonical_and_not_future(tmp_path, target):
    store, _research, _clock = setup(tmp_path)
    with pytest.raises(StockResearchError):
        store.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=target, rule_version='v1')


def test_repeat_refresh_survives_member_exit_and_has_deduplicated_symbols(tmp_path):
    store, research, _clock = setup(tmp_path)
    seed_member(store, research)
    seed_member(store, research, 'growth_trend')
    queued = store.enqueue(kind='refresh_members', pool_ids=['dividend_value', 'growth_trend'], target_date=TODAY, rule_version='v1')
    assert queued['input_snapshot']['symbols'] == ['600900']
    with research._write() as connection:
        connection.execute("UPDATE stocks_memberships SET status='exited'")
    assert store.enqueue(kind='refresh_members', pool_ids=['dividend_value', 'growth_trend'], target_date=TODAY, rule_version='v1')['run_id'] == queued['run_id']


def test_completion_requires_nonempty_latest_success_and_finite_artifact(tmp_path):
    store, _research, _clock = setup(tmp_path)
    store.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=TODAY, rule_version='v1')
    run = store.claim('worker')
    assert run is not None
    args = {'lease_token': run['lease_token']}
    with pytest.raises(StockResearchError):
        store.finish(run['run_id'], 'worker', status='completed', **args)
    with pytest.raises(StockResearchError):
        store.record_item(run['run_id'], 'worker', {'scope': 'a', 'status': 'completed', 'summary': '成功', 'artifact': {'value': float('nan')}}, **args)
    store.record_item(run['run_id'], 'worker', {'scope': 'a', 'status': 'partial', 'summary': '部分'}, **args)
    with pytest.raises(StockResearchError):
        store.finish(run['run_id'], 'worker', status='completed', **args)
    store.record_item(run['run_id'], 'worker', {'scope': 'a', 'status': 'completed', 'summary': '成功'}, **args)
    assert store.finish(run['run_id'], 'worker', status='completed', **args)['status'] == 'completed'


def test_list_page_is_bounded_and_automatic_disabled(tmp_path):
    store, _research, _clock = setup(tmp_path)
    with pytest.raises(StockResearchError):
        store.list_runs(page_size=101)
    assert store.list_runs(page=10**100)['items'] == []
    assert store.list_runs()['automatic_enabled'] is False


def test_concurrent_enqueue_and_claim_are_single_winner(tmp_path):
    store, _research, _clock = setup(tmp_path)
    barrier = Barrier(2)

    def enqueue():
        barrier.wait()
        return store.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=TODAY, rule_version='v1')

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: enqueue(), range(2)))
    assert results[0]['run_id'] == results[1]['run_id']
    store.enqueue(kind='discover_candidates', pool_ids=['growth_trend'], target_date=TODAY, rule_version='v1')

    def claim(worker):
        barrier.wait()
        return store.claim(worker)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ('a', 'b')))
    assert sum(claim is not None for claim in claims) == 1


def test_snapshot_and_insert_share_one_write_transaction(tmp_path, monkeypatch):
    store, research, _clock = setup(tmp_path)
    seed_member(store, research)
    original = store._snapshot
    writer_started, writer_finished = Event(), Event()

    def exit_member():
        writer_started.set()
        with research._write() as connection:
            connection.execute("UPDATE stocks_memberships SET status='exited'")
            connection.execute('UPDATE stocks_pool_revisions SET revision=1')
        writer_finished.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = []

        def snapshot(connection, kind, pools):
            assert connection.in_transaction
            futures.append(executor.submit(exit_member))
            assert writer_started.wait(2)
            assert not writer_finished.wait(0.05)
            return original(connection, kind, pools)

        monkeypatch.setattr(store, '_snapshot', snapshot)
        queued = store.enqueue(kind='refresh_members', pool_ids=['dividend_value'], target_date=TODAY, rule_version='v1')
        futures[0].result(timeout=5)
    assert queued['input_snapshot']['pool_revisions'] == {'dividend_value': 0}
    assert queued['input_snapshot']['members'][0]['status'] == 'active'
    assert store.get(queued['run_id'])['input_snapshot'] == queued['input_snapshot']


def test_partial_retry_preserves_history_and_skips_successful_scopes(tmp_path):
    store, _research, clock = setup(tmp_path)
    store.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=TODAY, rule_version='v1')
    first = store.claim('worker')
    assert first is not None
    for scope, status in [('a', 'completed'), ('b', 'partial')]:
        store.record_item(first['run_id'], 'worker', {'scope': scope, 'status': status, 'summary': '原始结果'}, lease_token=first['lease_token'])
    clock[0] += timedelta(seconds=91)
    assert store.recover_expired() == 1
    resumed = store.claim('worker')
    assert resumed is not None
    assert resumed['completed_scopes'] == ['a'] and resumed['pending_scopes'] == ['b']
    store.record_item(resumed['run_id'], 'worker', {'scope': 'b', 'status': 'completed', 'summary': '重试成功'}, lease_token=resumed['lease_token'])
    result = store.finish(resumed['run_id'], 'worker', status='completed', lease_token=resumed['lease_token'])
    assert [item['status'] for item in result['results']] == ['completed', 'partial', 'completed']
    with pytest.raises(StockRevisionConflict):
        store.heartbeat(resumed['run_id'], 'worker', lease_token=resumed['lease_token'])


def test_heartbeat_extends_only_live_lease_and_error_must_be_text(tmp_path):
    store, _research, clock = setup(tmp_path)
    store.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=TODAY, rule_version='v1')
    run = store.claim('worker')
    assert run is not None
    clock[0] += timedelta(seconds=80)
    store.heartbeat(run['run_id'], 'worker', lease_token=run['lease_token'])
    clock[0] += timedelta(seconds=20)
    assert store.recover_expired() == 0
    invalid_error: Any = {'bad': True}
    with pytest.raises(StockResearchError):
        store.finish(run['run_id'], 'worker', status='failed', error=invalid_error, lease_token=run['lease_token'])
    clock[0] += timedelta(seconds=70)
    with pytest.raises(StockRevisionConflict):
        store.heartbeat(run['run_id'], 'worker', lease_token=run['lease_token'])

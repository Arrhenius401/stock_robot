"""正式成员生命周期必须独立于候选、草案和ETF数据。"""

from datetime import UTC, date, datetime

import pytest

from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.membership_store import StockMembershipStore
from radar.stocks.pools import StockPoolRepository
from radar.stocks.research_store import (
    StockResearchError,
    StockResearchStore,
    StockRevisionConflict,
)
from stock_robot.stock_admission import evaluate_store

AS_OF = date(2026, 10, 15)


def setup(tmp_path):
    research = StockResearchStore(tmp_path/'research.db', clock=lambda: datetime(2026, 10, 15, 1, tzinfo=UTC))
    research.import_companies([{'symbol': '600900', 'name': '合成公司'}], source_sha256='a'*64, source_label='合成', data_as_of=AS_OF)
    archive = StockEvaluationStore(research)
    saved = archive.save_evaluation(evaluate_store(research, AS_OF, include_styles=True), archive.capture_inputs())
    directory = tmp_path/'pools'
    directory.mkdir()
    for index, pool_id in enumerate(('dividend_value', 'growth_trend')):
        (directory/f'{pool_id}.yaml').write_text(f'id: {pool_id}\nname: 合成池\ndescription: 合成\ndisplay_order: {index}\n', encoding='utf-8')
    return StockMembershipStore(archive, StockPoolRepository(directory)), research, saved['evaluation_id']


def seed_active(store, research, *, pool_id='dividend_value', revision=0):
    now = research._now().isoformat()
    with research._write() as connection:
        connection.execute('INSERT INTO stocks_pool_revisions VALUES(?,?)', (pool_id, revision))
        connection.execute('INSERT INTO stocks_memberships VALUES(?,?,?,?,?,?,?,?)', (pool_id, '600900', 'active', 'e'*64, 1, '合成初始成员', now, 1))


def change(action, pool_id='dividend_value'):
    return [{'pool_id': pool_id, 'symbol': '600900', 'action': action, 'reason': '合成审核理由'}]


def test_add_is_rejected_until_formal_screening_is_enabled(tmp_path):
    store, _research, evaluation_id = setup(tmp_path)
    with pytest.raises(StockResearchError, match='正式筛选尚未启用'):
        store.apply(evaluation_id=evaluation_id, changes=change('add'), expected_pool_revisions={'dividend_value': 0}, request_id='add-1', actor='审核人')
    assert store.list_members('dividend_value')['total'] == 0


def test_suspend_exit_and_event_history_are_append_only(tmp_path):
    store, research, _ = setup(tmp_path)
    seed_active(store, research, revision=4)
    suspended = store.apply(evaluation_id=None, changes=change('suspend'), expected_pool_revisions={'dividend_value': 4}, request_id='suspend-1', actor='审核人')
    assert suspended['changes'][0]['status'] == 'suspended'
    exited = store.apply(evaluation_id=None, changes=change('exit'), expected_pool_revisions={'dividend_value': 5}, request_id='exit-1', actor='审核人')
    member = store.get_member('dividend_value', '600900')
    assert member is not None and member['status'] == 'exited' and member['revision'] == 3
    assert member['source_evaluation_id'] == 'e'*64 and member['source_revision'] == 1
    assert [event['operation'] for event in member['events']] == ['suspend', 'exit']
    assert exited['pool_revisions'] == {'dividend_value': 6}


def test_restore_requires_current_formal_evaluation_and_keeps_member_exited(tmp_path):
    store, research, evaluation_id = setup(tmp_path)
    seed_active(store, research)
    store.apply(evaluation_id=None, changes=change('exit'), expected_pool_revisions={'dividend_value': 0}, request_id='exit-1', actor='审核人')
    with pytest.raises(StockResearchError, match='正式筛选尚未启用'):
        store.apply(evaluation_id=evaluation_id, changes=change('restore'), expected_pool_revisions={'dividend_value': 1}, request_id='restore-1', actor='审核人')
    member = store.get_member('dividend_value', '600900')
    assert member is not None and member['status'] == 'exited'


def test_stale_pool_revision_rejects_without_new_event(tmp_path):
    store, research, _ = setup(tmp_path)
    seed_active(store, research, revision=2)
    with pytest.raises(StockRevisionConflict):
        store.apply(evaluation_id=None, changes=change('suspend'), expected_pool_revisions={'dividend_value': 1}, request_id='stale', actor='审核人')
    member = store.get_member('dividend_value', '600900')
    assert member is not None and member['events'] == []


def test_same_request_is_idempotent_and_changed_request_conflicts(tmp_path):
    store, research, _ = setup(tmp_path)
    seed_active(store, research)
    first = store.apply(evaluation_id=None, changes=change('suspend'), expected_pool_revisions={'dividend_value': 0}, request_id='same', actor='审核人')
    again = store.apply(evaluation_id=None, changes=change('suspend'), expected_pool_revisions={'dividend_value': 0}, request_id='same', actor='审核人')
    assert first == again
    with pytest.raises(StockRevisionConflict):
        store.apply(evaluation_id=None, changes=change('exit'), expected_pool_revisions={'dividend_value': 1}, request_id='same', actor='审核人')


def test_completed_request_replays_after_evaluation_expires_and_pool_is_disabled(tmp_path):
    store, research, evaluation_id = setup(tmp_path)
    seed_active(store, research)
    arguments = {'evaluation_id': evaluation_id, 'changes': change('suspend'),
                 'expected_pool_revisions': {'dividend_value': 0}, 'request_id': 'stable-replay', 'actor': '审核人'}
    first = store.apply(**arguments)
    assert first['formal_screening_enabled'] is False
    assert first['members_published'] == 0
    research._clock = lambda: datetime(2026, 10, 16, 1, tzinfo=UTC)
    pool_path = store.pools.directory/'dividend_value.yaml'
    pool_path.write_text(pool_path.read_text(encoding='utf-8') + 'enabled: false\n', encoding='utf-8')
    assert store.apply(**arguments) == first
    member = store.get_member('dividend_value', '600900')
    assert member is not None and len(member['events']) == 1
    arguments['actor'] = '另一审核人'
    with pytest.raises(StockRevisionConflict, match='请求标识'):
        store.apply(**arguments)


def test_first_request_still_validates_current_evaluation(tmp_path):
    store, research, evaluation_id = setup(tmp_path)
    seed_active(store, research)
    research._clock = lambda: datetime(2026, 10, 16, 1, tzinfo=UTC)
    with pytest.raises(StockResearchError, match='historical_as_of'):
        store.apply(evaluation_id=evaluation_id, changes=change('suspend'), expected_pool_revisions={'dividend_value': 0}, request_id='expired-first', actor='审核人')
    member = store.get_member('dividend_value', '600900')
    assert member is not None and member['status'] == 'active' and member['events'] == []


@pytest.mark.parametrize('unconfigured', [None, False, '', {}, ['待配置']])
def test_future_publication_guard_requires_explicit_empty_rules(tmp_path, unconfigured):
    store, _research, _ = setup(tmp_path)
    # 仅直接探测守卫；真实研究档案仍禁止开启正式筛选。
    pool = {'approved_conditions_status': 'passed'}
    if unconfigured is not None:
        pool['unconfigured_rules'] = unconfigured
    snapshot = {'report': {'formal_screening_enabled': True, 'items': [
        {'symbol': '600900', 'source_revision': 1, 'common_status': 'passed', 'pools': {'dividend_value': pool}},
    ]}}
    with pytest.raises(StockResearchError, match='规则配置未满足'):
        store._eligible(snapshot, change('add')[0])


def test_two_pool_memberships_are_independent(tmp_path):
    store, research, _ = setup(tmp_path)
    seed_active(store, research, pool_id='dividend_value')
    seed_active(store, research, pool_id='growth_trend')
    store.apply(evaluation_id=None, changes=change('suspend', 'dividend_value'), expected_pool_revisions={'dividend_value': 0}, request_id='one-pool', actor='审核人')
    member = store.get_member('growth_trend', '600900')
    assert member is not None and member['status'] == 'active'


@pytest.mark.parametrize('status', ['active', 'suspended', 'exited', 'bad'])
def test_list_filters_status_and_validates_it(tmp_path, status):
    store, research, _ = setup(tmp_path)
    seed_active(store, research)
    if status == 'bad':
        with pytest.raises(StockResearchError):
            store.list_members('dividend_value', status=status)
    else:
        assert store.list_members('dividend_value', status=status)['total'] == (1 if status == 'active' else 0)


def test_no_publication_when_all_conditions_pass_and_reference_rules_are_nonblocking(tmp_path):
    store, _research, _evaluation_id = setup(tmp_path)
    snapshot = {'report': {'formal_screening_enabled': False, 'items': [
        {'symbol': '600900', 'source_revision': 1, 'common_status': 'passed',
         'pools': {'dividend_value': {'approved_conditions_status': 'passed', 'unconfigured_rules': []}}}]}}
    with pytest.raises(StockResearchError, match='正式筛选尚未启用'):
        store._eligible(snapshot, change('add')[0])
    assert store.list_members('dividend_value')['total'] == 0

"""研究评估档案的不可变性、来源版本与自然失效测试。"""

from datetime import UTC, date, datetime, timedelta

import pytest

from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.research_store import (
    StockResearchError,
    StockResearchStore,
    StockRevisionConflict,
)
from stock_robot.stock_admission import evaluate_store

NOW = datetime(2026, 10, 15, 3, tzinfo=UTC)
AS_OF = date(2026, 10, 15)


def setup(tmp_path):
    clock = [NOW]
    research = StockResearchStore(tmp_path / 'records.db', clock=lambda: clock[0])
    research.import_companies([{'symbol': '600900', 'name': '合成公司'}], source_sha256='a'*64,
                              source_label='合成', data_as_of=AS_OF)
    return research, StockEvaluationStore(research), clock


def capture(store, research):
    inputs = store.capture_inputs()
    report = evaluate_store(research, AS_OF, include_styles=True)
    return inputs, report


def test_snapshot_full_source_and_idempotency_excludes_capture_time(tmp_path):
    research, store, clock = setup(tmp_path)
    inputs, report = capture(store, research)
    saved = store.save_evaluation(report, inputs)
    assert saved['status'] == 'complete'
    assert saved['inputs']['companies'][0]['evidence'][0]['payload']['name'] == '合成公司'
    assert saved['freshness']['current']
    clock[0] += timedelta(minutes=1)
    again = store.save_evaluation(report, store.capture_inputs())
    assert saved['evaluation_id'] == again['evaluation_id']
    assert again['created_at'] == saved['created_at']
    assert store.list_evaluations()['total'] == 1


def test_source_change_blocks_save_without_partial_write(tmp_path):
    research, store, _clock = setup(tmp_path)
    inputs, report = capture(store, research)
    research.add_evidence('600900', kind='manual', payload={'value': None}, source_url='https://static.cninfo.com.cn/a.pdf',
                          disclosed_on=AS_OF, content_sha256='b'*64, expected_revision=1)
    with pytest.raises(StockRevisionConflict):
        store.save_evaluation(report, inputs)
    assert store.list_evaluations()['total'] == 0


def test_all_roster_change_blocks_but_single_scope_not_invalidated(tmp_path):
    research, store, _clock = setup(tmp_path)
    all_inputs, all_report = capture(store, research)
    single_inputs = store.capture_inputs(symbol='600900')
    single_report = evaluate_store(research, AS_OF, symbol='600900', include_styles=True)
    research.import_companies([{'symbol': '000333'}], source_sha256='b'*64, source_label='另股', data_as_of=AS_OF)
    with pytest.raises(StockRevisionConflict):
        store.save_evaluation(all_report, all_inputs)
    assert store.save_evaluation(single_report, single_inputs)['freshness']['current']


def test_partial_never_replaces_complete(tmp_path):
    research, store, _clock = setup(tmp_path)
    inputs, report = capture(store, research)
    complete = store.save_evaluation(report, inputs)
    report['errors'] = [{'symbol': '600900', 'reason': '合成执行失败'}]
    report['items'][0]['evaluation_valid'] = False
    partial = store.save_evaluation(report, inputs)
    assert partial['status'] == 'partial'
    loaded = store.get(complete['evaluation_id'])
    assert loaded is not None and loaded['status'] == 'complete'
    with pytest.raises(StockResearchError):
        store.assert_current(partial['evaluation_id'])


def test_dynamic_freshness_date_and_revision(tmp_path):
    research, store, clock = setup(tmp_path)
    inputs, report = capture(store, research)
    saved = store.save_evaluation(report, inputs)
    assert store.assert_current(saved['evaluation_id'])['evaluation_id'] == saved['evaluation_id']
    clock[0] += timedelta(days=1)
    loaded = store.get(saved['evaluation_id'])
    assert loaded is not None and not loaded['freshness']['current']
    with pytest.raises(StockResearchError):
        store.assert_current(saved['evaluation_id'])
    clock[0] = NOW
    research.add_evidence('600900', kind='manual', payload={'value': 1}, source_url='https://static.cninfo.com.cn/a.pdf',
                          disclosed_on=AS_OF, content_sha256='c'*64, expected_revision=1)
    with pytest.raises(StockRevisionConflict):
        store.assert_current(saved['evaluation_id'])


@pytest.mark.parametrize('mutation', ['formal', 'published', 'ready', 'eligible', 'rule', 'cross_company', 'revision'])
def test_invalid_reports_are_rejected(tmp_path, mutation):
    research, store, _clock = setup(tmp_path)
    inputs, report = capture(store, research)
    if mutation == 'formal':
        report['formal_screening_enabled'] = True
    elif mutation == 'published':
        report['members_published'] = 1
    elif mutation in {'ready', 'eligible'}:
        report['items'][0]['pools']['growth_trend']['admission_status'] = mutation
    elif mutation == 'rule':
        report['style_rule_version'] = 'future-version'
    elif mutation == 'cross_company':
        report['items'][0]['symbol'] = '000333'
    else:
        report['items'][0]['source_revision'] = 99
    with pytest.raises(StockResearchError):
        store.save_evaluation(report, inputs)
    assert store.list_evaluations()['total'] == 0


def test_snapshot_reopens_and_returns_independent_copy(tmp_path):
    research, store, _clock = setup(tmp_path)
    inputs, report = capture(store, research)
    saved = store.save_evaluation(report, inputs)
    saved['report']['items'][0]['common_status'] = 'tampered'
    reopened = StockEvaluationStore(research).get(saved['evaluation_id'])
    assert reopened is not None
    assert reopened['report']['items'][0]['common_status'] == 'pending_verification'
    assert 'report' not in store.list_evaluations()['items'][0]
    assert store.list_evaluations(symbol='000333')['total'] == 0
    assert store.list_evaluations(symbol='600900')['total'] == 1
    assert store.list_evaluations(page=10**100, page_size=10**100)['items'] == []


def test_archive_preserves_original_review_after_new_decision(tmp_path):
    research, store, _clock = setup(tmp_path)
    added = research.add_evidence('600900', kind='admission_governance', payload={'verdict': 'clear', 'basis': '合成'},
                                 source_url='https://static.cninfo.com.cn/a.pdf', disclosed_on=AS_OF,
                                 content_sha256='b'*64, expected_revision=1)
    research.save_review('600900', added['evidence_id'], expected_revision=2, decision='confirmed', reviewer='合成审核', reason='仅用于测试')
    inputs, report = capture(store, research)
    saved = store.save_evaluation(report, inputs)
    research.save_review('600900', added['evidence_id'], expected_revision=3, decision='rejected', reviewer='合成审核', reason='后续新判断')
    loaded = store.get(saved['evaluation_id'])
    assert loaded is not None
    row = next(row for row in loaded['inputs']['companies'][0]['evidence'] if row['evidence_id'] == added['evidence_id'])
    assert row['latest_review']['decision'] == 'confirmed'
    assert not loaded['freshness']['current']


def test_rule_version_and_review_natural_expiration_are_dynamic(tmp_path, monkeypatch):
    from radar.stocks import admission

    research, store, clock = setup(tmp_path)
    clock[0] = NOW - timedelta(days=89, hours=23)
    added = research.add_evidence('600900', kind='manual', payload={'value': 1},
                                 source_url='https://static.cninfo.com.cn/a.pdf', disclosed_on=date(2026, 7, 1),
                                 content_sha256='b'*64, expected_revision=1)
    research.save_review('600900', added['evidence_id'], expected_revision=2, decision='confirmed', reviewer='合成', reason='仅测试到期')
    clock[0] = NOW
    inputs, report = capture(store, research)
    saved = store.save_evaluation(report, inputs)
    assert saved['freshness']['current']
    clock[0] += timedelta(hours=1)
    loaded = store.get(saved['evaluation_id'])
    assert loaded is not None
    assert any(reason.startswith('review_expired:') for reason in loaded['freshness']['reasons'])
    clock[0] = NOW
    monkeypatch.setattr(admission, 'RULE_VERSION', 'changed-version')
    loaded = store.get(saved['evaluation_id'])
    assert loaded is not None and 'rule_version_changed' in loaded['freshness']['reasons']


def test_tampered_manifest_and_report_evidence_are_not_source_authority(tmp_path):
    research, store, _clock = setup(tmp_path)
    inputs, report = capture(store, research)
    inputs['companies'][0]['evidence'][0]['payload']['name'] = '伪造原文'
    with pytest.raises(StockRevisionConflict):
        store.save_evaluation(report, inputs)
    assert store.list_evaluations()['total'] == 0


def test_assert_current_can_be_used_inside_existing_write_transaction(tmp_path):
    research, store, _clock = setup(tmp_path)
    inputs, report = capture(store, research)
    saved = store.save_evaluation(report, inputs)
    with research._write():
        assert store.assert_current(saved['evaluation_id'])['freshness']['current']


def test_independent_feature_version_and_etf_pragma(tmp_path):
    research, _store, _clock = setup(tmp_path)
    with research._write() as connection:
        connection.execute('PRAGMA user_version=39')
        connection.execute('UPDATE stocks_evaluation_schema_versions SET version=2')
    with pytest.raises(StockResearchError):
        StockEvaluationStore(research)
    with research._write() as connection:
        assert connection.execute('SELECT version FROM stocks_schema_versions').fetchone()[0] == 1
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 39


def test_capture_rejects_concurrent_revision_change(tmp_path, monkeypatch):
    research, store, _clock = setup(tmp_path)
    original = research.list_evidence
    def changed(symbol):
        rows = original(symbol)
        research.add_evidence('600900', kind='manual', payload={'value': 1}, source_url='https://static.cninfo.com.cn/a.pdf',
                              disclosed_on=AS_OF, content_sha256='b'*64, expected_revision=1)
        return rows
    monkeypatch.setattr(research, 'list_evidence', changed)
    with pytest.raises(StockRevisionConflict):
        store.capture_inputs()


@pytest.mark.parametrize('change', ['wrong_reference', 'missing_pool', 'pool_null', 'missing_validity', 'scope_list'])
def test_structural_source_and_result_errors_are_rejected(tmp_path, change):
    research, store, _clock = setup(tmp_path)
    inputs, report = capture(store, research)
    item = report['items'][0]
    if change == 'wrong_reference':
        item['pools']['growth_trend']['checks'][0]['evidence_ids'] = ['f'*64]
    elif change == 'missing_pool':
        del item['pools']['growth_trend']
    elif change == 'pool_null':
        item['pools']['growth_trend'] = None
    elif change == 'missing_validity':
        del item['evaluation_valid']
    else:
        inputs['scope'] = []
    with pytest.raises(StockResearchError):
        store.save_evaluation(report, inputs)
    assert store.list_evaluations()['total'] == 0


def test_nested_invalid_result_is_partial_even_if_top_is_valid(tmp_path):
    research, store, _clock = setup(tmp_path)
    inputs, report = capture(store, research)
    report['items'][0]['pools']['growth_trend']['evaluation_valid'] = False
    assert store.save_evaluation(report, inputs)['status'] == 'partial'

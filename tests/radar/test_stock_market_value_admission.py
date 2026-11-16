"""供应商市值与已审核共同资格的保守合并。"""
from copy import deepcopy

import pytest

from radar.stocks.admission import evaluate_common_admission
from radar.stocks.market_value_admission import merge_common_market_value
from radar.stocks.style import evaluate_stock_styles
from stock_robot.stock_admission import evaluate_store
from tests.radar.test_stock_style import AS_OF, WINDOW, facts, group


@pytest.mark.parametrize('case', ['pass', 'manual_failed', 'supplier_failed', 'missing', 'historical'])
def test_market_value_merge_retains_sources_and_propagates(case):
    evidence = facts()
    if case == 'manual_failed':
        capital = group(evidence, 'admission_capitalization')
        capital['current']['amount_yuan'] = '1'
        capital['daily'][-1]['amount_yuan'] = '1'
    manual = evaluate_common_admission('600900', evidence, AS_OF, market_window=WINDOW)
    original = deepcopy(manual)
    checks = [{'code': code, 'outcome': 'passed', 'reason': '供应商总市值'}
              for code in ('capitalization_current', 'capitalization_average_60d')]
    if case == 'supplier_failed':
        checks[0]['outcome'] = 'failed'
    source = {'status': 'current', 'as_of': AS_OF.isoformat(), 'checks': checks}
    if case == 'missing':
        source['checks'] = []
    if case == 'historical':
        source.update(status='historical', as_of='2026-10-14')
    merged = merge_common_market_value(manual, source, AS_OF)
    assert manual == original
    current = next(row for row in merged['checks'] if row['code'] == 'capitalization_current')
    assert current['manual_check'] and current['supplier_check']
    assert current['outcome'] == ('passed' if case == 'pass' else 'pending_verification')
    assert merged['common_status'] == ('failed' if case == 'manual_failed' else
                                       'passed' if case == 'pass' else 'pending_verification')
    if case == 'historical':
        assert current['reference_as_of'] == '2026-10-14'
        assert current['reference_check']['outcome'] == 'passed'
    styled = evaluate_stock_styles('600900', evidence, AS_OF, market_window=WINDOW, common_result=merged)
    assert all(pool['approved_conditions_status'] == merged['common_status'] for pool in styled['pools'].values())
    assert styled['formal_screening_enabled'] is False and styled['members_published'] == 0


def test_missing_supplier_does_not_erase_existing_manual_failure():
    evidence = facts()
    capital = group(evidence, 'admission_capitalization')
    capital['current']['amount_yuan'] = '1'
    capital['daily'][-1]['amount_yuan'] = '1'
    manual = evaluate_common_admission('600900', evidence, AS_OF, market_window=WINDOW)
    merged = merge_common_market_value(manual, {'status': 'unavailable'}, AS_OF)
    current = next(row for row in merged['checks'] if row['code'] == 'capitalization_current')
    assert current['outcome'] == 'failed'
    assert merged['common_status'] == 'failed'


def test_historical_reference_retains_manual_failure_reason():
    evidence = facts()
    capital = group(evidence, 'admission_capitalization')
    capital['current']['amount_yuan'] = capital['daily'][-1]['amount_yuan'] = '1'
    manual = evaluate_common_admission('600900', evidence, AS_OF, market_window=WINDOW)
    source = {'status': 'historical', 'as_of': '2026-10-14', 'checks': [
        {'code': 'capitalization_current', 'outcome': 'passed'}]}
    merged = merge_common_market_value(manual, source, AS_OF)
    current = next(row for row in merged['checks'] if row['code'] == 'capitalization_current')
    assert current['outcome'] == 'failed' and '保留已知人工失败' in current['reason']
    assert current['reference_check']['outcome'] == 'passed'


def test_evaluate_store_explicit_market_source_and_legacy_compatibility(tmp_path):
    from radar.stocks.research_store import StockResearchStore

    store = StockResearchStore(tmp_path / 'research.db')
    store.import_companies([{'symbol': '600900', 'name': '长江电力'}], source_sha256='a' * 64,
                           source_label='合成', data_as_of=AS_OF)
    before = evaluate_store(store, AS_OF, symbol='600900', market_window=WINDOW)['items'][0]
    assert 'market_value_source' not in before
    source = {'status': 'current', 'as_of': AS_OF.isoformat(), 'checks': [
        {'code': code, 'outcome': 'passed'} for code in ('capitalization_current', 'capitalization_average_60d')]}
    after = evaluate_store(store, AS_OF, symbol='600900', market_window=WINDOW,
                           market_value_sources={'600900': source}, include_styles=True)['items'][0]
    checks = {row['code']: row for row in after['checks']}
    assert checks['capitalization_current']['outcome'] == 'passed'
    assert checks['capitalization_average_60d']['outcome'] == 'passed'
    assert after['market_value_source'] == source and after['formal_screening_enabled'] is False


def test_archive_rejects_unbound_market_source(tmp_path):
    from radar.stocks.evaluation_store import StockEvaluationStore
    from radar.stocks.research_store import StockResearchError, StockResearchStore

    store = StockResearchStore(tmp_path / 'research.db')
    store.import_companies([{'symbol': '600900', 'name': '长江电力'}], source_sha256='a' * 64,
                           source_label='合成', data_as_of=AS_OF)
    archive = StockEvaluationStore(store)
    inputs = archive.capture_inputs(symbol='600900')
    source = {'status': 'unavailable', 'as_of': AS_OF.isoformat(), 'checks': []}
    report = evaluate_store(store, AS_OF, symbol='600900', market_window=WINDOW,
                            market_value_sources={'600900': source}, include_styles=True)
    with pytest.raises(StockResearchError, match='市值来源'):
        archive.save_evaluation(report, inputs, market_window=WINDOW)


def test_archive_invalidates_when_market_source_changes(tmp_path, monkeypatch):
    from radar.stocks.evaluation_store import StockEvaluationStore
    from radar.stocks.research_store import StockResearchStore, StockRevisionConflict

    store = StockResearchStore(tmp_path / 'research.db')
    store.import_companies([{'symbol': '600900', 'name': '长江电力'}], source_sha256='a' * 64,
                           source_label='合成', data_as_of=AS_OF)
    archive = StockEvaluationStore(store)
    source = {'symbol': '600900', 'evaluation_as_of': AS_OF.isoformat(), 'status': 'unavailable', 'checks': []}
    live = [source]
    monkeypatch.setattr('radar.stocks.evaluation_store.load_market_value_source', lambda *_, **__: live[0])
    inputs = archive.capture_inputs(symbol='600900')
    inputs['companies'][0].update(market_value_source=source, market_value_as_of=AS_OF.isoformat())
    report = evaluate_store(store, AS_OF, symbol='600900', market_window=WINDOW,
                            market_value_sources={'600900': source}, include_styles=True)
    saved = archive.save_evaluation(report, inputs, market_window=WINDOW)
    live[0] = {**source, 'manifest_sha256': 'b' * 64}
    with pytest.raises(StockRevisionConflict, match='评估来源已变化'):
        archive.assert_current(saved['evaluation_id'])


def test_archive_rejects_mismatched_current_market_window(tmp_path, monkeypatch):
    from radar.stocks.evaluation_store import StockEvaluationStore
    from radar.stocks.research_store import StockResearchError, StockResearchStore

    store = StockResearchStore(tmp_path / 'research.db')
    store.import_companies([{'symbol': '600900', 'name': '长江电力'}], source_sha256='a' * 64,
                           source_label='合成', data_as_of=AS_OF)
    archive = StockEvaluationStore(store)
    source = {'symbol': '600900', 'evaluation_as_of': AS_OF.isoformat(), 'as_of': AS_OF.isoformat(),
              'status': 'current', 'window_days': [day.isoformat() for day in WINDOW],
              'calendar_sha256': 'a' * 64, 'checks': []}
    monkeypatch.setattr('radar.stocks.evaluation_store.load_market_value_source', lambda *_, **__: source)
    inputs = archive.capture_inputs(symbol='600900')
    inputs['companies'][0].update(market_value_source=source, market_value_as_of=AS_OF.isoformat())
    report = evaluate_store(store, AS_OF, symbol='600900', market_window=WINDOW,
                            market_value_sources={'600900': source}, include_styles=True)
    report['market_window_source'] = {'calendar': {'snapshot_sha256': 'b' * 64}}
    with pytest.raises(StockResearchError, match='市值.*日历'):
        archive.save_evaluation(report, inputs, market_window=WINDOW)

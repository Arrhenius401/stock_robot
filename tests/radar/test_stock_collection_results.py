"""股票专用取数保留原时间和边界，不将错误字典视为有效数据。"""
from datetime import datetime

import pytest

from radar.stocks.collection_results import collection_result


@pytest.mark.parametrize('kind', ['financial', 'index_scope', 'market_history', 'capital_history'])
def test_all_failed_is_failed_and_diagnostics_are_safe(kind):
    result = collection_result({'status': 'partial', 'items': [{'status': 'source_error', 'error': '/secret?key=token'}],
                                'records': {}, 'sources': {}}, kind)
    assert result.record.result_state == 'failed'
    assert result.record.collected_at is None
    assert 'secret' not in result.record.model_dump_json()


def test_cache_timestamp_is_original_and_mixed_success_is_partial():
    result = collection_result({'status': 'partial', 'items': [
        {'status': 'cached', 'fetched_at': '2026-10-18T08:00:00+08:00', 'rows': [{'date': '2026-10-16'}]},
        {'status': 'source_error'}]}, 'market_history')
    assert result.record.result_state == 'partial'
    assert result.value is not None
    records = result.value['collection_records']
    assert records[0]['cache_status'] == 'cache'
    assert datetime.fromisoformat(records[0]['collected_at']).day == 18
    assert records[1]['collected_at'] is None
    assert result.record.collected_at is None


def test_missing_time_and_cached_financial_do_not_invent_dates():
    value = {'status': 'complete', 'records': {'main': [{}]}, 'sources': {
        'main': {'cache_status': 'cache', 'fetched_at': '2026-10-18T08:00:00+08:00'},
        'profit': {}, 'cashflow': {}}}
    result = collection_result(value, 'financial')
    assert result.value is not None
    assert result.value['collection_records'][0]['cache_status'] == 'cache'
    assert result.value['collection_records'][1]['collected_at'] is None


@pytest.mark.parametrize('kind', ['financial', 'index_scope', 'market_history', 'capital_history'])
def test_false_complete_without_data_is_not_success(kind):
    assert collection_result({'status': 'complete', 'items': [], 'records': {}, 'companies': []}, kind).record.result_state != 'complete'


def test_empty_success_is_distinct_from_transport_failure():
    value = {'status': 'complete', 'items': [{'status': 'downloaded', 'rows': [], 'complete': True}]}
    assert collection_result(value, 'capital_history').record.result_state == 'empty'


@pytest.mark.parametrize('kind,key', [('market_history', 'window_complete'), ('capital_history', 'complete')])
def test_rejected_all_rows_is_failure_not_normal_empty(kind, key):
    result = collection_result({'status': 'partial', 'items': [{'status': 'downloaded', key: False,
        'rows': [], 'excluded_rows': [{'reason': '证券身份不符'}]}]}, kind)
    assert result.record.result_state == 'failed'
    assert result.value is not None
    assert result.value['collection_records'][0]['error_kind'] == 'invalid_data'

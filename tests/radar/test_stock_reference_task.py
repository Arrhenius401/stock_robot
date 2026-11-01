"""基础资料冻结范围及独立任务来源回归。"""
import json
from datetime import date

import pytest

from radar.stocks.reference_task import REFERENCE_VERSION, reference_snapshot
from tests.radar.test_stock_financial_source import environment

DAY = date(2026, 10, 18)


def scope_document():
    from radar.stocks.index_scope import POOL_INDEX_CODES
    return {'version': 'official-index-scope-v1', 'as_of': '2026-10-16', 'status': 'complete', 'scope_complete': True,
            'companies': [{'symbol': '600900', 'name': '长江电力', 'source_indexes': ['000510'], 'pool_ids': list(POOL_INDEX_CODES)}],
            'pools': {p: {'status': 'complete', 'complete': True, 'index_codes': list(codes), 'companies': ['600900']} for p, codes in POOL_INDEX_CODES.items()}}


def snapshot(pools=None):
    return reference_snapshot(scope_document(), pools or ['growth_trend'], DAY, source_run_id='original-scope', source_sha256='a' * 64)


def test_two_pools_freeze_one_company_and_reject_tampering(tmp_path):
    _, _, tasks, _ = environment(tmp_path)
    frozen = snapshot(['dividend_value', 'growth_trend'])
    assert len(frozen['companies']) == 1
    run = tasks.enqueue(kind='reference_review', pool_ids=frozen['pool_ids'], target_date=DAY,
                        rule_version=REFERENCE_VERSION, snapshot=frozen)
    assert run['input_snapshot'] == frozen
    frozen['companies'] = []
    with pytest.raises(ValueError):
        tasks.enqueue(kind='reference_review', pool_ids=frozen['pool_ids'], target_date=DAY,
                      rule_version=REFERENCE_VERSION, snapshot=frozen)


def test_complete_selected_pool_does_not_require_unrequested_pool(tmp_path):
    from radar.stocks.market_value_source import _scope
    document = scope_document()
    document.update(status='partial', scope_complete=False)
    document['pools']['dividend_value'].update(status='partial', complete=False)
    frozen = reference_snapshot(document, ['growth_trend'], DAY, source_run_id='scope', source_sha256='a' * 64)
    assert frozen['scope_document']['selected_pool_ids'] == ['growth_trend']
    assert frozen['scope_document']['pools']['dividend_value']['complete'] is False
    _scope(frozen['scope_document'], '600900', DAY)
    _, _, tasks, _ = environment(tmp_path)
    tasks.enqueue(kind='reference_review', pool_ids=['growth_trend'], target_date=DAY,
                  rule_version=REFERENCE_VERSION, snapshot=frozen)
    with pytest.raises(ValueError):
        reference_snapshot(document, ['dividend_value'], DAY, source_run_id='scope', source_sha256='a' * 64)
    del frozen['scope_document']['selected_pool_ids']
    with pytest.raises(ValueError, match='所选池'):
        _scope(frozen['scope_document'], '600900', DAY)


def test_unprocessed_task_blocks_all_legacy_channels(tmp_path):
    from radar.stocks.company_profile_source import load_company_profile_source
    from radar.stocks.industry_source import load_industry_source
    from radar.stocks.market_value_registry import load_market_value_source
    _, _, tasks, _ = environment(tmp_path)
    run = tasks.enqueue(kind='reference_review', pool_ids=['growth_trend'], target_date=DAY,
                        rule_version=REFERENCE_VERSION, snapshot=snapshot())
    for loader in (load_company_profile_source, load_industry_source, load_market_value_source):
        result = loader(tmp_path, '600900', DAY)
        assert result['status'] == 'unavailable'
        assert result['reference_task']['run_id'] == run['run_id']
        assert str(tmp_path) not in json.dumps(result)


def test_unknown_or_broken_latest_task_does_not_disappear(tmp_path):
    from radar.stocks.industry_source import load_industry_source
    _, _, tasks, _ = environment(tmp_path)
    run = tasks.enqueue(kind='reference_review', pool_ids=['growth_trend'], target_date=DAY,
                        rule_version=REFERENCE_VERSION, snapshot=snapshot())
    with tasks.research._write() as connection:
        connection.execute('UPDATE stocks_update_runs SET input_snapshot=?', ('{}',))
    result = load_industry_source(tmp_path, '600900', DAY)
    assert result['status'] == 'unavailable' and result['reference_task']['run_id'] == run['run_id']


def test_deep_corrupted_task_is_unavailable_instead_of_detail_error(tmp_path):
    from radar.stocks.industry_source import load_industry_source
    _, _, tasks, _ = environment(tmp_path)
    tasks.enqueue(kind='reference_review', pool_ids=['growth_trend'], target_date=DAY,
                  rule_version=REFERENCE_VERSION, snapshot=snapshot())
    # 独立审查提供的原始异常样本：深层数组让json解码触发RecursionError。
    with tasks.research._write() as connection:
        connection.execute('UPDATE stocks_update_runs SET input_snapshot=?', ('[' * 50000 + ']' * 50000,))
    assert load_industry_source(tmp_path, '600900', DAY)['status'] == 'unavailable'

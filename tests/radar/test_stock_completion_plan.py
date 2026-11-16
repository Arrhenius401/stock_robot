"""补全计划：只读、确定性、无模型。"""
from datetime import date

from radar.stocks.completion_plan import build_completion_plan

DAY = date(2026, 11, 7)


def details():
    return [
        {'代码': '002384', '名称': '东山精密', '池': 'dividend_value', '分类': 'common',
         '条件': 'financial_quality', '结果': 'passed', '原因': '共同条件通过'},
        {'代码': '002352', '名称': '顺丰控股', '池': 'dividend_value', '分类': 'common',
         '条件': 'financial_quality', '结果': 'passed', '原因': '共同条件通过'},
        {'代码': '002155', '名称': '湖南黄金', '池': 'growth_trend', '分类': 'common',
         '条件': 'financial_quality', '结果': 'passed', '原因': '共同条件通过'},
        {'代码': '002384', '名称': '东山精密', '池': 'dividend_value', '分类': 'style',
         '条件': 'dividend_history', '结果': 'pending_verification', '原因': '分红来源不足'},
        {'代码': '002352', '名称': '顺丰控股', '池': 'dividend_value', '分类': 'style',
         '条件': 'cash_review', '结果': 'pending_verification', '原因': '三年现金字段缺失'},
        {'代码': '002155', '名称': '湖南黄金', '池': 'growth_trend', '分类': 'style',
         '条件': 'liquidity', '结果': 'unavailable', '原因': '成交额缺失'},
        {'代码': '600000', '名称': '浦发银行', '池': 'dividend_value', '分类': 'common',
         '条件': 'governance', '结果': 'pending_verification', '原因': '共同条件未通过'},
        {'代码': '600000', '名称': '浦发银行', '池': 'dividend_value', '分类': 'style',
         '条件': 'dividend_history', '结果': 'pending_verification', '原因': '不应进入计划'},
    ]


def test_plan_is_deterministic_read_only_and_never_uses_model(tmp_path):
    db = tmp_path / 'missing.db'
    plan = build_completion_plan(details(), db, DAY)
    again = build_completion_plan(details(), db, DAY)
    assert plan['plan_sha256'] == again['plan_sha256']
    assert not db.exists()
    assert plan['summary']['symbols'] == 3
    assert plan['summary']['by_condition'] == {'cash_review': 1, 'dividend_history': 1, 'liquidity': 1}
    assert all(item['proof_required'] for item in plan['items'])
    assert all('expected' in item and 'source' in item for item in plan['items'])


def test_plan_filters_pool_and_symbol(tmp_path):
    plan = build_completion_plan(details(), tmp_path / 'missing.db', DAY,
                                 pools={'dividend_value'}, symbols={'002352'})
    assert [item['symbol'] for item in plan['items']] == ['002352']


def test_dividend_plan_exposes_missing_years(tmp_path):
    plan = build_completion_plan(details(), tmp_path / 'missing.db', DAY)
    dividend = next(item for item in plan['items'] if item['condition'] == 'dividend_history')
    assert dividend['gap']['years'] == [2023, 2024, 2025]
    assert dividend['expected'] == 'failed_if_confirmed_no_dividend_else_pending'


def test_plan_hash_changes_with_items(tmp_path):
    base = build_completion_plan(details(), tmp_path / 'missing.db', DAY)
    fewer = build_completion_plan(details()[1:], tmp_path / 'missing.db', DAY)
    assert base['plan_sha256'] != fewer['plan_sha256']

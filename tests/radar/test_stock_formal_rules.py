"""已确认治理与财务质量结构规则的合并边界。"""
from datetime import date

from radar.stocks.admission import RULE_VERSION

DAY = date(2026, 11, 7)


def common():
    return {'symbol': '600000', 'rule_version': RULE_VERSION,
            'checks': [
                {'code': 'security', 'outcome': 'passed', 'values': {'issuer_type': 'ordinary'}, 'evidence_ids': []},
                {'code': 'governance', 'outcome': 'pending_verification', 'reason': '待核验', 'evidence_ids': []},
                {'code': 'financial_quality', 'outcome': 'pending_verification', 'reason': '待核验', 'evidence_ids': []},
            ]}


def check(result, code):
    return next(row for row in result['checks'] if row['code'] == code)


def test_company_object_discipline_is_hard_failure():
    from radar.stocks.formal_rules import merge_governance

    source = {'status': 'current', 'symbol': '600000', 'as_of': DAY.isoformat(), 'records': [
        {'listed_on': '2025-01-01', 'measure_category': 'discipline', 'object': '上市公司,董监高',
         'title': '纪律处分决定'}]}
    result = merge_governance(common(), source, DAY)
    assert check(result, 'governance')['outcome'] == 'failed'
    assert result['common_status'] == 'failed'


def test_person_only_discipline_and_measure_remain_pending():
    from radar.stocks.formal_rules import merge_governance

    source = {'status': 'current', 'symbol': '600000', 'as_of': DAY.isoformat(), 'records': [
        {'listed_on': '2025-01-01', 'measure_category': 'discipline', 'object': '董事', 'title': '个人处分'},
        {'listed_on': '2025-02-01', 'measure_category': 'regulatory_measure', 'object': '上市公司',
         'title': '监管警示'}]}
    result = merge_governance(common(), source, DAY)
    assert check(result, 'governance')['outcome'] == 'pending_verification'


def test_fraud_or_fund_occupation_is_hard_failure_even_in_work_letter():
    from radar.stocks.formal_rules import merge_governance

    source = {'status': 'current', 'symbol': '600000', 'as_of': DAY.isoformat(), 'records': [
        {'listed_on': '2024-01-01', 'measure_category': 'work_letter', 'object': '上市公司',
         'title': '关于资金占用事项的监管工作函'}]}
    result = merge_governance(common(), source, DAY)
    assert check(result, 'governance')['outcome'] == 'failed'


def test_governance_unavailable_does_not_pass_or_override_failure():
    from radar.stocks.formal_rules import merge_governance

    result = merge_governance(common(), {'status': 'unavailable'}, DAY)
    assert check(result, 'governance')['outcome'] == 'pending_verification'
    failed = common()
    check(failed, 'governance')['outcome'] = 'failed'
    assert check(merge_governance(failed, {'status': 'unavailable'}, DAY), 'governance')['outcome'] == 'failed'


def test_bank_financial_quality_thresholds():
    from radar.stocks.formal_rules import merge_financial_quality

    source = {'status': 'current', 'symbol': '600000', 'as_of': DAY.isoformat(),
              'financial': {'type_metrics': {'status': 'available', 'report_date': '2025-12-31',
                                             'bank_car': 12.0, 'bank_provision': 180.0, 'bank_npl_ratio': 0.02}}}
    result = merge_financial_quality(common(), source, DAY, 'bank')
    assert check(result, 'financial_quality')['outcome'] == 'passed'
    source['financial']['type_metrics']['bank_car'] = 9.0
    assert check(merge_financial_quality(common(), source, DAY, 'bank'), 'financial_quality')['outcome'] == 'failed'


def test_securities_and_insurance_thresholds():
    from radar.stocks.formal_rules import merge_financial_quality

    sec = {'status': 'current', 'symbol': '600000', 'as_of': DAY.isoformat(),
           'financial': {'type_metrics': {'status': 'available', 'sec_risk_coverage': 120.0,
                                          'sec_liquidity_coverage': 130.0}}}
    assert check(merge_financial_quality(common(), sec, DAY, 'securities'), 'financial_quality')['outcome'] == 'passed'
    ins = {'status': 'current', 'symbol': '600000', 'as_of': DAY.isoformat(),
           'financial': {'type_metrics': {'status': 'available', 'insurance_solvency': 180.0}}}
    assert check(merge_financial_quality(common(), ins, DAY, 'insurance'), 'financial_quality')['outcome'] == 'passed'


def test_ordinary_financial_quality_does_not_use_cash_ratio():
    from radar.stocks.formal_rules import merge_financial_quality

    base = common()
    base['checks'] = [
        {'code': 'security', 'outcome': 'passed', 'values': {'issuer_type': 'ordinary'}, 'evidence_ids': []},
        {'code': 'audit', 'outcome': 'passed', 'evidence_ids': []},
        {'code': 'annual_parent_profit_2025', 'outcome': 'passed', 'evidence_ids': []},
        {'code': 'ttm_parent_profit', 'outcome': 'passed', 'evidence_ids': []},
        {'code': 'financial_quality', 'outcome': 'pending_verification', 'evidence_ids': []},
    ]
    source = {'status': 'current', 'symbol': '600000', 'as_of': DAY.isoformat(),
              'financial': {'cash_flow_reference': {'reference_condition_status': 'failed'}}}
    assert check(merge_financial_quality(base, source, DAY, 'ordinary'), 'financial_quality')['outcome'] == 'passed'
    base['checks'][1]['outcome'] = 'failed'
    assert check(merge_financial_quality(base, source, DAY, 'ordinary'), 'financial_quality')['outcome'] == 'failed'

def test_bank_capital_uses_total_capital_adequacy_field():
    from radar.stocks.financial_source import type_specific_metrics

    collection = {'records': {'main': [{'REPORT_DATE': '2025-12-31 00:00:00',
                                        'NEWCAPITALADER': '12.12', 'FIRST_ADEQUACY_RATIO': '9.60',
                                        'BLDKBBL': '155.37', 'NON_PERFORMING_LOAN': '26037000000',
                                        'GROSSLOANS': '1913489000000'}]}}
    metrics = type_specific_metrics(collection, DAY)
    assert metrics['bank_car'] == 12.12
    assert metrics['bank_tier1'] == 9.6

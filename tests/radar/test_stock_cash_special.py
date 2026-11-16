"""现金专项：普通企业分红优先，证券类型分支，银行/保险待专用字段。"""
from datetime import date

from radar.stocks.cash_special import cash_special_verdict

DAY = date(2026, 11, 7)


def source(metrics=None):
    return {'symbol': '600000', 'status': 'current', 'as_of': DAY.isoformat(), 'evaluation_as_of': DAY.isoformat(),
            'financial': {
        'source_company_type': '4',
        'cash_special': metrics or {'status': 'available', 'years': [2023, 2024, 2025], 'ocf_sum': 300.0,
                                    'capex_sum': 80.0, 'dividend_sum': 100.0, 'cash_end': [10.0, 20.0, 30.0],
                                    'interest_coverage': 5.0, 'capex_exceeds_ocf': False, 'currency': 'CNY',
                                    'unit': 'CNY_yuan', 'coverage': 'dividends_and_interest'},
        'type_metrics': {'status': 'available', 'report_date': '2025-12-31', 'currency': 'CNY',
                         'sec_risk_coverage': 120.0, 'sec_liquidity_coverage': 130.0}}}


def test_ordinary_cash_special_dividend_priority():
    assert cash_special_verdict(source(), 'ordinary', DAY, '600000')['outcome'] == 'passed'
    bad = source()
    bad['financial']['cash_special']['ocf_sum'] = 50.0
    assert cash_special_verdict(bad, 'ordinary', DAY, '600000')['outcome'] == 'failed'


def test_ordinary_cash_special_missing_is_pending():
    bad = source({'status': 'pending_verification', 'reason': '缺少年度数据'})
    assert cash_special_verdict(bad, 'ordinary', DAY, '600000')['outcome'] == 'pending_verification'


def test_securities_and_bank_cash_special():
    sec = source()
    sec['financial']['source_company_type'] = '1'
    assert cash_special_verdict(sec, 'securities', DAY, '600000')['outcome'] == 'passed'
    assert cash_special_verdict(source(), 'bank', DAY, '600000')['outcome'] == 'not_applicable'
    assert cash_special_verdict(source(), 'insurance', DAY, '600000')['outcome'] == 'not_applicable'
    assert cash_special_verdict(source(), 'other_financial', DAY, '600000')['outcome'] == 'not_applicable'


def test_unverified_issuer_is_pending():
    assert cash_special_verdict(source(), None, DAY, '600000')['outcome'] == 'pending_verification'


def test_wrong_symbol_or_stale_source_is_pending():
    assert cash_special_verdict(source(), 'ordinary', DAY, '600001')['outcome'] == 'pending_verification'
    stale = source()
    stale['evaluation_as_of'] = '2026-11-06'
    assert cash_special_verdict(stale, 'ordinary', DAY, '600000')['outcome'] == 'pending_verification'


def test_cash_special_accepts_end_cce_when_end_cash_absent():
    from radar.stocks.cash_special import cash_special_metrics

    rows = [{'SECURITY_CODE': '600000', 'CURRENCY': 'CNY', 'REPORT_DATE': f'{year}-12-31 00:00:00',
             'NOTICE_DATE': f'{year + 1}-03-20 00:00:00', 'UPDATE_DATE': f'{year + 1}-03-20 00:00:00',
             'NETCASH_OPERATE': '10', 'CONSTRUCT_LONG_ASSET': '1', 'ASSIGN_DIVIDEND_PORFIT': '2',
             'END_CASH': None, 'END_CASH_EQUIVALENTS': None, 'END_CCE': '5'}
            for year in (2023, 2024, 2025)]
    collection = {'records': {'cashflow': rows,
                              'main': [{'SECURITY_CODE': '600000', 'CURRENCY': 'CNY',
                                        'REPORT_DATE': '2025-12-31 00:00:00',
                                        'NOTICE_DATE': '2026-03-20 00:00:00', 'UPDATE_DATE': '2026-03-20 00:00:00',
                                        'INTEREST_COVERAGE_RATIO': '5'}]}}
    metrics = cash_special_metrics(collection, DAY, '600000')
    assert metrics['status'] == 'available'
    assert metrics['cash_end'] == [5.0, 5.0, 5.0]


def test_cash_special_derives_interest_when_net_finance_expense_nonpositive():
    from radar.stocks.cash_special import cash_special_metrics

    rows = [{'SECURITY_CODE': '600000', 'CURRENCY': 'CNY', 'REPORT_DATE': f'{year}-12-31 00:00:00',
             'NOTICE_DATE': f'{year + 1}-03-20 00:00:00', 'UPDATE_DATE': f'{year + 1}-03-20 00:00:00',
             'NETCASH_OPERATE': '10', 'CONSTRUCT_LONG_ASSET': '1', 'ASSIGN_DIVIDEND_PORFIT': '2', 'END_CASH': '5'}
            for year in (2023, 2024, 2025)]
    main = [{'SECURITY_CODE': '600000', 'CURRENCY': 'CNY', 'REPORT_DATE': '2025-12-31 00:00:00',
             'NOTICE_DATE': '2026-03-20 00:00:00', 'UPDATE_DATE': '2026-03-20 00:00:00'}]
    profit = [{'SECURITY_CODE': '600000', 'CURRENCY': 'CNY', 'REPORT_DATE': '2025-12-31 00:00:00',
               'NOTICE_DATE': '2026-03-20 00:00:00', 'UPDATE_DATE': '2026-03-20 00:00:00',
               'FINANCE_EXPENSE': '-1', 'TOTAL_PROFIT': '10'}]
    metrics = cash_special_metrics({'records': {'cashflow': rows, 'main': main, 'profit': profit}}, DAY, '600000')
    assert metrics['status'] == 'available'
    assert metrics['interest_coverage'] == 1.0
    assert metrics['interest_basis'] == 'net_finance_expense_nonpositive'


def test_missing_dividend_field_is_pending_not_zero():
    from radar.stocks.cash_special import cash_special_metrics

    rows = [{'SECURITY_CODE': '600000', 'CURRENCY': 'CNY', 'REPORT_DATE': f'{year}-12-31 00:00:00',
             'NOTICE_DATE': f'{year + 1}-03-20 00:00:00', 'UPDATE_DATE': f'{year + 1}-03-20 00:00:00',
             'NETCASH_OPERATE': '10', 'CONSTRUCT_LONG_ASSET': '1', 'END_CASH': '5'}
            for year in (2023, 2024, 2025)]
    collection = {'records': {'cashflow': rows,
                              'main': [{'SECURITY_CODE': '600000', 'CURRENCY': 'CNY',
                                        'REPORT_DATE': '2025-12-31 00:00:00',
                                        'NOTICE_DATE': '2026-03-20 00:00:00', 'UPDATE_DATE': '2026-03-20 00:00:00',
                                        'INTEREST_COVERAGE_RATIO': '5'}]}}
    assert cash_special_metrics(collection, DAY, '600000')['status'] == 'pending_verification'


def test_cash_special_rejects_other_company_missing_notice_and_duplicate_year():
    from radar.stocks.cash_special import cash_special_metrics

    rows = [{'SECURITY_CODE': '600000', 'CURRENCY': 'CNY', 'REPORT_DATE': f'{year}-12-31 00:00:00',
             'NOTICE_DATE': f'{year + 1}-03-20 00:00:00', 'UPDATE_DATE': f'{year + 1}-03-20 00:00:00',
             'NETCASH_OPERATE': '10', 'CONSTRUCT_LONG_ASSET': '1', 'ASSIGN_DIVIDEND_PORFIT': '2', 'END_CASH': '5'}
            for year in (2023, 2024, 2025)]
    main = [{'SECURITY_CODE': '600000', 'CURRENCY': 'CNY', 'REPORT_DATE': '2025-12-31 00:00:00',
             'NOTICE_DATE': '2026-03-20 00:00:00', 'UPDATE_DATE': '2026-03-20 00:00:00',
             'INTEREST_COVERAGE_RATIO': '5'}]
    good = {'records': {'cashflow': rows, 'main': main}}
    assert cash_special_metrics(good, DAY, '600000')['status'] == 'available'
    other = {'records': {'cashflow': [dict(row, SECURITY_CODE='000001') for row in rows], 'main': main}}
    assert cash_special_metrics(other, DAY, '600000')['status'] == 'pending_verification'
    missing = {'records': {'cashflow': [dict(row, NOTICE_DATE='') for row in rows], 'main': main}}
    assert cash_special_metrics(missing, DAY, '600000')['status'] == 'pending_verification'
    duplicate = {'records': {'cashflow': [*rows, dict(rows[-1])], 'main': main}}
    assert cash_special_metrics(duplicate, DAY, '600000')['status'] == 'pending_verification'

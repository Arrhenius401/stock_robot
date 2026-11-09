"""现金专项：普通企业分红优先，证券类型分支，银行/保险待专用字段。"""
from datetime import date

from radar.stocks.cash_special import cash_special_verdict

DAY = date(2026, 11, 7)


def source(metrics=None):
    return {'status': 'current', 'as_of': DAY.isoformat(), 'financial': {
        'cash_special': metrics or {'status': 'available', 'ocf_sum': 300.0, 'capex_sum': 80.0,
                                    'dividend_sum': 100.0, 'cash_end': [10.0, 20.0, 30.0],
                                    'interest_coverage': 5.0, 'capex_exceeds_ocf': False},
        'type_metrics': {'status': 'available', 'sec_risk_coverage': 120.0, 'sec_liquidity_coverage': 130.0}}}


def test_ordinary_cash_special_dividend_priority():
    assert cash_special_verdict(source(), 'ordinary', DAY)['outcome'] == 'passed'
    bad = source()
    bad['financial']['cash_special']['ocf_sum'] = 50.0
    assert cash_special_verdict(bad, 'ordinary', DAY)['outcome'] == 'failed'


def test_ordinary_cash_special_missing_is_pending():
    bad = source({'status': 'pending_verification', 'reason': '缺少年度数据'})
    assert cash_special_verdict(bad, 'ordinary', DAY)['outcome'] == 'pending_verification'


def test_securities_and_bank_cash_special():
    assert cash_special_verdict(source(), 'securities', DAY)['outcome'] == 'passed'
    assert cash_special_verdict(source(), 'bank', DAY)['outcome'] == 'pending_verification'
    assert cash_special_verdict(source(), 'insurance', DAY)['outcome'] == 'pending_verification'


def test_unverified_issuer_is_pending():
    assert cash_special_verdict(source(), None, DAY)['outcome'] == 'pending_verification'

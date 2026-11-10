"""年报利润分配章节独立判定：不分红/分红/待核验。"""
from radar.stocks.dividend_plan import classify_dividend_plan_text

DAY = '2026-04-20'


def classify(text, symbol='600000', year=2025):
    return classify_dividend_plan_text(text, symbol=symbol, source_url='https://static.cninfo.com.cn/a.pdf',
                                       disclosed_on=DAY, year=year)


def test_no_dividend_plan_is_failed_not_pending():
    text = '公司2025年度利润分配预案为：不派发现金红利，不送红股，不以公积金转增股本。'
    result = classify(text)
    assert result['outcome'] == 'no_dividend'
    assert result['no_dividend_phrase'] == '不派发现金红利'


def test_positive_plan_is_detected():
    text = '公司2025年度利润分配预案为：每10股派发现金红利5.00元（含税）。'
    result = classify(text)
    assert result['outcome'] == 'positive'
    assert result['positive_amounts'] == ['5.00']


def test_conflicting_or_unknown_plan_stays_pending():
    assert classify('公司2025年度利润分配预案为：不派发现金红利；同时每10股派发现金红利1元。')['outcome'] == 'pending'
    assert classify('公司2025年度报告。')['outcome'] == 'pending'
    assert classify('')['outcome'] == 'pending'


def test_three_user_cases_are_covered_by_text_rules_not_hardcoded_symbols():
    assert classify('2025年度利润分配预案：不派发现金红利，不送红股，不以公积金转增股本。')['outcome'] == 'no_dividend'
    assert classify('2024年度利润分配预案：不派发现金红利，不送红股。')['outcome'] == 'no_dividend'
    assert classify('2023年年度利润分配预案：每10股派发现金红利6.00元（含税）。')['outcome'] == 'positive'

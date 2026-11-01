"""成长研究累计差分、正基数和冲突边界。"""
from datetime import date

from radar.stocks.growth_research import calculate_growth_research


def rows():
    return [{'SECURITY_CODE': '600900', 'REPORT_DATE': period, 'NOTICE_DATE': notice,
             'UPDATE_DATE': notice, 'CURRENCY': 'CNY', 'TOTALOPERATEREVE': revenue, 'KCFJCXSYJLR': profit}
            for period, notice, revenue, profit in [
                ('2023-06-30', '2023-08-20', '200', '20'),
                ('2023-12-31', '2024-03-01', '400', '40'),
                ('2024-03-31', '2024-04-20', '100', '10'),
                ('2024-06-30', '2024-08-20', '200', '20'),
                ('2024-12-31', '2025-03-01', '440', '44'),
                ('2025-03-31', '2025-04-20', '120', '15'),
                ('2025-06-30', '2025-08-20', '250', '32')]]


def test_two_quarters_and_ttm_reference_paths():
    result = calculate_growth_research('600900', rows(), date(2025, 10, 18), date(2025, 6, 30))
    assert result['paths']['sustained_growth'] == 'met_reference'
    assert result['paths']['operating_improvement'] == 'met_reference'
    assert result['quarters'][0]['revenue']['current'] == '120'
    assert result['quarters'][1]['revenue']['current'] == '130'
    assert result['research_only'] and result['members_published'] == 0


def test_conflicting_revision_and_nonpositive_base_are_pending():
    data = rows()
    data.append({**data[-1], 'TOTALOPERATEREVE': '999'})
    result = calculate_growth_research('600900', data, date(2025, 10, 18), date(2025, 6, 30))
    assert result['paths']['sustained_growth'] == 'pending_verification'
    data = rows()
    next(row for row in data if row['REPORT_DATE'] == '2024-03-31')['KCFJCXSYJLR'] = '-1'
    result = calculate_growth_research('600900', data, date(2025, 10, 18), date(2025, 6, 30))
    assert result['quarters'][0]['deducted_profit']['yoy_percent'] is None


def test_future_disclosure_and_currency_mismatch_are_not_used():
    data = rows()
    data[-1]['NOTICE_DATE'] = '2025-10-19'
    result = calculate_growth_research('600900', data, date(2025, 10, 18), date(2025, 6, 30))
    assert result['paths']['sustained_growth'] == 'pending_verification'
    data[-1]['NOTICE_DATE'] = '2025-08-20'
    data[-1]['CURRENCY'] = 'USD'
    assert calculate_growth_research('600900', data, date(2025, 10, 18), date(2025, 6, 30))['paths']['sustained_growth'] == 'pending_verification'


def test_q1_uses_previous_year_q4_annual_minus_q3():
    data = rows()
    for year, revenue, profit in [(2023, '300', '30'), (2024, '320', '32')]:
        data.append({'SECURITY_CODE': '600900', 'REPORT_DATE': f'{year}-09-30',
                     'NOTICE_DATE': f'{year}-10-20', 'UPDATE_DATE': f'{year}-10-20',
                     'CURRENCY': 'CNY', 'TOTALOPERATEREVE': revenue, 'KCFJCXSYJLR': profit})
    result = calculate_growth_research('600900', data, date(2025, 5, 1), date(2025, 3, 31))
    assert [row['period'] for row in result['quarters']] == ['2024-12-31', '2025-03-31']
    assert result['quarters'][0]['revenue']['current'] == '120'
    assert result['quarters'][0]['revenue']['prior'] == '100'
    assert result['quarters'][0]['deducted_profit']['current'] == '12'

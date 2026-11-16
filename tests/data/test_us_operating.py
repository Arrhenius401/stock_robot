"""连续单季TTM、缺项及独立经营分门槛。"""
from datetime import date, timedelta

import pytest

from analysis.industry_scoring import score_industry_comparison
from data.enrichers.us_industry_enricher import UsIndustryEnricher
from data.schemas import AnalysisContext, IndustryData
from data.us_operating import GENERAL, build_operating, load_operating


def rows():
    starts = ['2024-10-01', '2025-01-01', '2025-04-01', '2025-07-01',
              '2025-10-01', '2026-01-01', '2026-04-01', '2026-07-01']
    ends = ['2024-12-31', '2025-03-31', '2025-06-30', '2025-09-30',
            '2025-12-31', '2026-03-31', '2026-06-30', '2026-09-30']
    return [{'SECURITY_CODE': 'TEST', 'DATE_TYPE': '单季报', 'START_DATE': s, 'REPORT_DATE': e,
             'CURRENCY_ABBR': 'USD', 'OPERATE_INCOME': 100 if i < 4 else 120,
             'PARENT_HOLDER_NETPROFIT': 12, 'NOTICE_DATE': None} for i, (s, e) in enumerate(zip(starts, ends, strict=True))]


def test_ttm_actual_period_growth_and_margin():
    value = build_operating(rows(), 'TEST', GENERAL)
    assert value is not None
    assert value['revenue'] == 480
    assert value['revenue_growth_yoy'] == pytest.approx(20)
    assert value['net_margin'] == 10
    assert value['start'] == '2025-10-01'
    assert value['notice_dates'] == [None]*4


@pytest.mark.parametrize('failure', ['gap', 'duplicate', 'currency', 'identity', 'missing'])
def test_invalid_data_never_fills_zero(failure):
    data = rows()
    if failure == 'gap':
        data[-1]['START_DATE'] = '2026-07-02'
    elif failure == 'duplicate':
        data[-2] = data[-1]
    elif failure == 'currency':
        data[-1]['CURRENCY_ABBR'] = 'EUR'
    elif failure == 'identity':
        data[-1]['SECURITY_CODE'] = 'OTHER'
    else:
        data[-1]['OPERATE_INCOME'] = None
    assert build_operating(data, 'TEST', GENERAL) is None


def test_failure_and_brk_are_missing(mocker):
    mocker.patch('data.us_operating.fetch_operating', side_effect=ValueError('失败'))
    assert load_operating(['TEST']) == {'TEST': None}


@pytest.mark.parametrize('count,scored', [(5, True), (4, False), (2, False)])
def test_two_operating_metrics_each_need_five_peers(count, scored):
    operating = build_operating(rows(), 'TEST', GENERAL)
    quotes = {f'P{i}': {'symbol': f'P{i}', 'basis_verified': True, 'market_cap': 100, 'pe': 10, 'pb': 2} for i in range(count)}
    ctx = AnalysisContext(symbol='TEST', name='目标', market='us', industry_data=IndustryData(
        symbol='TEST', industry='软件', sector='', peers=list(quotes), comparison_snapshot={
            'target': {'basis_verified': True, 'market_cap': 100, 'pe': 10, 'pb': 2}, 'quotes': quotes,
            'operating': {'TEST': operating, **dict.fromkeys(quotes, operating)}}))
    UsIndustryEnricher().enrich(ctx)
    assert ctx.enriched_industry is not None
    comparison = ctx.enriched_industry.comparison
    assert (score_industry_comparison(comparison)[0] is not None) == scored
    assert all(p.roe is None for p in ctx.enriched_industry.top_peers)
    assert 'annual' not in comparison['periods']
    if scored:
        assert ctx.sufficiency.industry.level == 'sufficient'
    # 目标缺失或同行报告过旧都不能借估值数字计分。
    assert operating is not None
    operating['end'] = (date(2026, 9, 30) - timedelta(days=200)).isoformat()
    assert ctx.industry_data is not None
    ctx.industry_data.comparison_snapshot['operating']['TEST'] = None
    UsIndustryEnricher().enrich(ctx)
    assert score_industry_comparison(ctx.enriched_industry.comparison)[0] is None

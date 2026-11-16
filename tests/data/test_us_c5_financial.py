"""美股TTM事实必须有实际期间、币种和权益日期。"""
from datetime import date

import pytest

from analysis.scorers.general import GeneralScorer
from data.financial_scoring_facts import FinancialScoringFactsService
from data.schemas import AnalysisContext, FinancialData


def context():
    rows = []
    for start, end in [('2025-10-01', '2025-12-31'), ('2026-01-01', '2026-03-31'),
                       ('2026-04-01', '2026-06-30'), ('2026-07-01', '2026-09-30')]:
        rows.append(FinancialData(symbol='AAPL', fiscal_quarter=date.fromisoformat(end),
            period_start=date.fromisoformat(start), period_basis='single', net_profit=10,
            common_equity=100, total_equity=100, total_assets=200, financial_source='F10',
            field_positions={'net_profit': {'start': start, 'end': end, 'currency': 'USD'},
                             'common_equity': {'end': end, 'currency': 'USD'}}))
    return AnalysisContext(symbol='AAPL', name='苹果', market='us', financial_data=rows)


def test_ttm_roe_does_not_require_cumulative_or_snapshot(mocker):
    fetch = mocker.patch('data.financial_scoring_facts.fetch_financial_rows')
    ctx = context()
    FinancialScoringFactsService().enrich(ctx)
    assert ctx.financial_scoring_facts['annual_roe']['value'] == pytest.approx(.4)
    assert ctx.financial_scoring_facts['annual_roe']['denominator_date'] == '2026-09-30'
    fetch.assert_not_called()


@pytest.mark.parametrize('failure', ['gap', 'currency', 'equity', 'missing'])
def test_invalid_ttm_evidence_is_missing(failure):
    ctx = context()
    assert ctx.financial_data is not None
    if failure == 'gap':
        ctx.financial_data[1].field_positions['net_profit']['start'] = '2026-01-02'
    elif failure == 'currency':
        ctx.financial_data[1].field_positions['net_profit']['currency'] = 'EUR'
    elif failure == 'equity':
        ctx.financial_data[-1].common_equity = -1
    else:
        ctx.financial_data[1].net_profit = None
    FinancialScoringFactsService().enrich(ctx)
    assert ctx.financial_scoring_facts['annual_roe']['value'] is None


def test_single_debt_item_cannot_be_full_score_and_financial_sector_is_na():
    ctx = context()
    scorer = GeneralScorer({'financial': {'debt_ratio': {'tiers': [{'min': 0, 'max': 100, 'score': 2}]}}}, {})
    score, _, _ = scorer.score_financial(ctx)
    assert score is not None and score <= 2
    assert scorer.financial_score_basis['available_max'] < scorer.financial_score_basis['eligible_max']
    assert ctx.financial_data is not None
    ctx.financial_data[-1].gross_margin_basis = '银行/保险不适用'
    assert scorer.score_financial(ctx)[0] is None


def test_statement_metadata_preserves_real_dates_and_currency(mocker):
    from data.us_stock import _statement_metadata
    response = mocker.Mock()
    response.json.return_value = {'success': True, 'result': {'data': [
        {'SECURITY_CODE': 'AAPL', 'REPORT_DATE': '2026-09-26', 'START_DATE': '2026-06-28',
         'REPORT_TYPE': '单季报', 'CURRENCY_ABBR': 'USD', 'ACCOUNT_STANDARD': '美国会计准则'}]}}
    get = mocker.patch('data.us_stock.requests.get', return_value=response)
    value = _statement_metadata('AAPL', '综合损益表', '2025-01-01')
    assert value['2026-09-26']['start'] == '2026-06-28'
    assert value['2026-09-26']['currency'] == 'USD'
    assert "REPORT_DATE>='2025-01-01'" in get.call_args.kwargs['params']['filter']
    response.json.return_value = {'success': False, 'message': 'filter错误'}
    assert _statement_metadata('AAPL', '综合损益表', '2025-01-01') == {}

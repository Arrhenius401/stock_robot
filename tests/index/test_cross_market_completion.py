from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from analysis.mode import objective_results
from data.akshare import AkShareAdapter
from data.schemas import (
    AnalysisTarget,
    CapitalFlowData,
    IndexAnalysisContext,
    IndexValuationData,
)
from index.analysis.capital_flow import CapitalFlowAnalyzer
from index.analysis.valuation import IndexValuationAnalyzer
from index.build_single import IndexReportBuilder, render_index_report_markdown
from index.valuation_data import (
    CURRENT_PE_BASIS,
    CURRENT_URL,
    StrategyValuationProvider,
)

ANCHOR = date(2026, 11, 18)


@pytest.mark.parametrize(('symbol', 'name'), [
    ('000001', '上证指数'), ('000010', '上证180'), ('000016', '上证50'), ('000300', '沪深300'),
    ('000009', '上证380'), ('000015', '红利指数'), ('000922', '中证红利'), ('000905', '中证500'),
])
def test_current_snapshot(tmp_path, mocker, symbol, name):
    provider = StrategyValuationProvider(cache_path=tmp_path / 'cache.db')
    request = mocker.patch.object(provider, '_get_json', return_value={'data': {'indexValuations': [
        {'indexName': name, 'tradeDate': '20261117', 'pe': 13.74, 'peg': 13.05, 'pb': 1.28, 'dp': 3.05},
    ]}})
    assert provider.fetch_current('801780', end=ANCHOR) is None
    request.assert_not_called()
    result = provider.fetch_current(symbol, end=ANCHOR)
    assert result is not None
    assert result.pe_snapshot == 13.74 and result.pb == 1.28 and result.dividend_yield == 3.05
    assert result.date == result.pe_snapshot_as_of == result.pb_as_of == date(2026, 11, 17)
    assert result.pe_ttm is result.pe_percentile is result.pb_percentile is None
    assert result.valuation_valid is False
    request.assert_called_once_with(CURRENT_URL)


@pytest.mark.parametrize(('previous', 'change'), [(45, 5), (50, 0), (None, None)])
def test_margin_common_days(mocker, previous, change):
    mocker.patch('akshare.stock_margin_sse', return_value=pd.DataFrame([
        {'信用交易日期': day, '融资余额': 6_000_000_000} for day in ('20261118', '20261117', '20261116')
    ]))

    def szse(*, date):
        if date == '20261118':
            raise ValueError('非交易日或尚未发布')
        return pd.DataFrame([{'融资余额': 50 if date == '20261117' else previous}])

    mocker.patch('akshare.stock_margin_szse', side_effect=szse)
    north = mocker.patch('akshare.stock_hsgt_north_net_flow_in_em', create=True)
    row, = AkShareAdapter().fetch('000300', data_type='index_capital_flow', as_of=ANCHOR)
    assert row.margin_date == '2026-11-17'
    assert row.margin_balance == 110
    assert row.margin_change == change
    north.assert_not_called()


def test_margin_rejects_mismatched_days(mocker):
    mocker.patch('akshare.stock_margin_sse', return_value=pd.DataFrame([
        {'信用交易日期': '20261117', '融资余额': 6_000_000_000},
    ]))
    mocker.patch('akshare.stock_margin_szse', return_value=pd.DataFrame([
        {'日期': '20261116', '融资余额': 50},
    ]))
    fallback = mocker.patch('akshare.stock_hsgt_fund_flow_summary_em')
    assert not AkShareAdapter().fetch('801780', data_type='index_capital_flow', index_style='sector', as_of=ANCHOR)
    fallback.assert_not_called()


def test_old_capital_defaults():
    row = CapitalFlowData.model_validate({'symbol': '000300', 'date': '2026-11-17', 'north_bound': 1.0})
    assert row.north_bound == 1.0
    assert row.margin_date == ''
    assert row.margin_change is None


@pytest.mark.parametrize(('symbol', 'style'), [('000300', 'broad'), ('801780', 'sector')])
def test_margin_and_snapshot_remain_facts_in_default_report(symbol, style):
    day = datetime.now().astimezone().date() - timedelta(days=1)
    target = AnalysisTarget(target_type='index', symbol=symbol, name=symbol, market='a-shares', index_style=style)
    ctx = IndexAnalysisContext(target=target, capital_flow=CapitalFlowData(
        symbol=symbol, date=day, margin_date=day.isoformat(), margin_balance=22097.71, margin_change=0,
    ))
    if style == 'broad':
        ctx.valuation_data = IndexValuationData(symbol=symbol, date=day, pe_snapshot=13.74,
            pe_snapshot_basis=CURRENT_PE_BASIS, pb=1.28, dividend_yield=3.05, valuation_valid=False)
    results = objective_results([CapitalFlowAnalyzer().analyze(ctx), IndexValuationAnalyzer().analyze(ctx)])
    report = IndexReportBuilder().build(ctx, results)
    markdown = render_index_report_markdown(report)
    assert '22,097.71 亿元' in markdown and '变化 0.00 亿元' in markdown and str(day) in markdown
    assert '不代表指数净流入' in markdown and '北向' not in markdown
    if style == 'sector':
        assert '全 A 市场背景，不是行业资金流' in markdown
        assert not report.section_valuation['metrics']
    else:
        assert '官方PE（口径未声明） 13.74' in markdown and '股息率 3.05%' in markdown
        assert '不判断高低估' in markdown


def test_old_margin_date_only_provides_partial_background():
    day = datetime.now().astimezone().date() - timedelta(days=8)
    ctx = IndexAnalysisContext(
        target=AnalysisTarget(target_type='index', symbol='000300', name='沪深300', index_style='broad'),
        capital_flow=CapitalFlowData(symbol='000300', date=day, margin_date=str(day), margin_balance=110, margin_change=1),
    )
    result = CapitalFlowAnalyzer().analyze(ctx)
    assert result.status == 'partial'
    assert '仅作历史参考' in result.summary

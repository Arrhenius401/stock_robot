"""补全适配器：只整理现有来源结果，不重复抓取/校验。"""
from datetime import date, timedelta

from radar.stocks.completion_adapters import (
    AnnualReportCashflowAdapter,
    TurnoverRetryAdapter,
)
from radar.stocks.financial_extract import extract_cashflow_text

DAY = date(2026, 11, 7)


def cashflow_text():
    return (
        '合并现金流量表\n'
        '经营活动产生的现金流量净额 1,094,520,847.73\n'
        '购建固定资产、无形资产和其他长期资产支付的现金 553,057,873.74\n'
        '分配股利、利润或偿付利息支付的现金 168,462,000.00\n'
        '期末现金及现金等价物余额 286,438,980.59\n'
    )


def fields():
    return {'netcash_operate': 1094520847.73, 'construct_long_asset': 553057873.74,
            'assign_dividend_profit': 168462000.00, 'end_cash': 286438980.59}


def turnover_daily():
    start = DAY - timedelta(days=89)
    return [{'date': (start + timedelta(days=index)).isoformat(), 'amount_yuan': '120000000'}
            for index in range(60)]


def test_cashflow_text_parses_all_fields():
    parsed = extract_cashflow_text(cashflow_text())
    assert parsed['status'] == 'complete'
    assert parsed['fields']['assign_dividend_profit'] == 168462000.0


def test_cashflow_text_marks_missing_fields_pending():
    parsed = extract_cashflow_text('经营活动产生的现金流量净额 10')
    assert parsed['status'] == 'pending_verification'
    assert 'assign_dividend_profit' in parsed['missing']


def test_annual_adapter_consumes_provider_without_network():
    item = {'symbol': '603087', 'name': '甘李药业', 'gap': {'years': [2023]}}
    assert AnnualReportCashflowAdapter().collect(item, DAY, {}).status == 'pending_verification'
    result = AnnualReportCashflowAdapter().collect(
        item, DAY, {'annual_report': lambda symbol, name, years: {2023: {'fields': fields()}}})
    assert result.status == 'complete'
    assert result.evidence['years']['2023']['fields']['assign_dividend_profit'] == 168462000.0


def test_turnover_adapter_consumes_verified_payload():
    item = {'symbol': '002155'}
    assert TurnoverRetryAdapter().collect(item, DAY, {}).status == 'pending_verification'
    result = TurnoverRetryAdapter().collect(
        item, DAY, {'turnover': lambda symbol, as_of: {'daily': turnover_daily()}})
    assert result.status == 'complete'
    assert len(result.evidence['daily']) == 60


def test_turnover_adapter_keeps_incomplete_payload_pending():
    item = {'symbol': '002155'}
    result = TurnoverRetryAdapter().collect(
        item, DAY, {'turnover': lambda symbol, as_of: {'daily': turnover_daily()[:10]}})
    assert result.status == 'pending_verification'

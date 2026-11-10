"""三个补全适配器：解析、校验、待核验，不调用模型。"""
from datetime import date, timedelta

from radar.stocks.completion_adapters import (
    AnnualReportCashflowAdapter,
    SourceRetryAdapter,
    TurnoverRetryAdapter,
    parse_cashflow_text,
    parse_turnover_rows,
)

DAY = date(2026, 11, 7)


def cashflow_text():
    return (
        '合并现金流量表\n'
        '经营活动产生的现金流量净额 1,094,520,847.73\n'
        '购建固定资产、无形资产和其他长期资产支付的现金 553,057,873.74\n'
        '分配股利、利润或偿付利息支付的现金 84,000,000.00\n'
        '期末现金及现金等价物余额 286,438,980.59\n'
    )


def turnover_rows():
    start = DAY - timedelta(days=89)
    return [{'date': (start + timedelta(days=index)).isoformat(), 'amount_yuan': '120000000'}
            for index in range(60)]


def test_cashflow_text_parses_all_four_fields():
    parsed = parse_cashflow_text(cashflow_text(), 2023)
    assert parsed['status'] == 'complete'
    assert parsed['fields']['assign_dividend_profit'] == 84000000.0
    assert parsed['fields']['end_cash'] == 286438980.59


def test_cashflow_text_marks_missing_fields_pending():
    parsed = parse_cashflow_text('经营活动产生的现金流量净额 10', 2023)
    assert parsed['status'] == 'pending_verification'
    assert 'assign_dividend_profit' in parsed['missing']


def test_cashflow_adapter_uses_injected_fetch_and_never_guesses():
    item = {'symbol': '603087', 'gap': {'years': [2023]}}
    assert AnnualReportCashflowAdapter().collect(item, DAY).status == 'pending_verification'
    result = AnnualReportCashflowAdapter().collect(
        item, DAY, fetch=lambda symbol, years: {2023: cashflow_text()})
    assert result.status == 'complete'
    assert result.evidence['years']['2023']['fields']['assign_dividend_profit'] == 84000000.0


def test_turnover_requires_exactly_60_sorted_days():
    parsed = parse_turnover_rows(turnover_rows(), DAY)
    assert parsed['status'] == 'complete' and len(parsed['daily']) == 60
    bad = turnover_rows()
    bad[1] = dict(bad[0])
    assert parse_turnover_rows(bad, DAY)['status'] == 'pending_verification'
    assert parse_turnover_rows(turnover_rows()[:59], DAY)['status'] == 'pending_verification'


def test_turnover_adapter_uses_injected_fetch():
    item = {'symbol': '002155'}
    assert TurnoverRetryAdapter().collect(item, DAY).status == 'pending_verification'
    result = TurnoverRetryAdapter().collect(item, DAY, fetch=lambda symbol, as_of: turnover_rows())
    assert result.status == 'complete'
    assert result.evidence['window_end']


def test_source_retry_adapter_reports_complete_or_pending():
    item = {'symbol': '688981'}
    assert SourceRetryAdapter().collect(item, DAY).status == 'pending_verification'
    assert SourceRetryAdapter().collect(
        item, DAY, retry=lambda symbol, as_of: {'status': 'complete'}).status == 'complete'
    assert SourceRetryAdapter().collect(
        item, DAY, retry=lambda symbol, as_of: {'status': 'partial'}).status == 'pending_verification'

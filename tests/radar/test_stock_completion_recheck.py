"""补全回灌：completion-run 更新评估明细，不再手工改 CSV。"""
import csv
import json
from datetime import date, timedelta

from radar.stocks.completion_recheck import recheck_run


def daily():
    start = date(2026, 11, 7) - timedelta(days=89)
    return [{'date': (start + timedelta(days=i)).isoformat(), 'amount_yuan': '120000000'} for i in range(60)]


def fields():
    return {'netcash_operate': 1094520847.73, 'construct_long_asset': 553057873.74,
            'assign_dividend_profit': 168462000.00, 'end_cash': 286438980.59}


def write_details(path):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['代码', '名称', '池', '分类', '条件', '结果', '原因'])
        writer.writeheader()
        writer.writerow({'代码': '603087', '名称': '甘李药业', '池': 'growth_trend', '分类': 'style',
                         '条件': 'cash_review', '结果': 'pending_verification', '原因': '缺字段'})
        writer.writerow({'代码': '002155', '名称': '湖南黄金', '池': 'growth_trend', '分类': 'style',
                         '条件': 'liquidity', '结果': 'unavailable', '原因': '缺成交额'})


def test_recheck_updates_two_conditions_from_run(tmp_path):
    details = tmp_path / 'details.csv'
    write_details(details)
    run = {'version': 'v2', 'as_of': '2026-11-07', 'items': [
        {'symbol': '603087', 'condition': 'cash_review', 'source_kind': 'annual_report_cashflow',
         'status': 'complete', 'evidence': {'years': {'2023': {'fields': fields()}}}},
        {'symbol': '002155', 'condition': 'liquidity', 'source_kind': 'market_turnover_60d',
         'status': 'complete', 'evidence': {'daily': daily()}},
    ]}
    run_path = tmp_path / 'run.json'
    run_path.write_text(json.dumps(run), encoding='utf-8')
    output = tmp_path / 'overrides.json'
    result = recheck_run(run_path, details, output)
    assert len(result['overrides']) == 2
    rows = list(csv.DictReader(details.open(encoding='utf-8-sig', newline='')))
    assert {row['代码']: row['结果'] for row in rows} == {'603087': 'passed', '002155': 'passed'}
    assert output.is_file()


def test_recheck_leaves_incomplete_run_untouched(tmp_path):
    details = tmp_path / 'details.csv'
    write_details(details)
    run = {'version': 'v2', 'as_of': '2026-11-07', 'items': [
        {'symbol': '603087', 'condition': 'cash_review', 'source_kind': 'annual_report_cashflow',
         'status': 'pending_verification'}]}
    run_path = tmp_path / 'run.json'
    run_path.write_text(json.dumps(run), encoding='utf-8')
    result = recheck_run(run_path, details)
    assert result['overrides'] == []

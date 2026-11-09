"""严格行业地位来源与合并边界。"""
import json
from datetime import date

from radar.stocks.leadership_source import load_leadership_source

DAY = date(2026, 11, 7)


def claim():
    return {'symbol': '000333', 'scope': '中央空调国内市场销售额', 'metric': 'share', 'value': '22%',
            'rank': 1, 'source_name': '产业在线',
            'quote': '产业在线数据显示,美的中央空调国内市场销售额占比超过22%,继续保持行业第一',
            'report_date': '2026-06-30',
            'source_url': 'https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax?code=SZ000333'}


def write(tmp_path, value):
    path = tmp_path / 'stock-leadership'; path.mkdir()
    (path / 'leadership.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def test_leadership_source_valid_and_missing(tmp_path):
    write(tmp_path, {'as_of': DAY.isoformat(), 'symbols': {'000333': [claim()]}})
    result = load_leadership_source(tmp_path, '000333', DAY)
    assert result['status'] == 'current' and result['claims']
    assert load_leadership_source(tmp_path, '600900', DAY)['status'] == 'current'
    assert load_leadership_source(tmp_path, '600900', DAY)['claims'] == []


def test_leadership_source_rejects_bad_metadata(tmp_path):
    bad = claim(); bad['quote'] = ''
    write(tmp_path, {'as_of': DAY.isoformat(), 'symbols': {'000333': [bad]}})
    assert load_leadership_source(tmp_path, '000333', DAY)['status'] == 'unavailable'


def test_leadership_merge_pass_and_pending():
    from radar.stocks.formal_rules import merge_leadership

    common = {'symbol': '000333', 'checks': [{'code': 'leadership', 'outcome': 'pending_verification', 'evidence_ids': []}]}
    passed = merge_leadership(common, {'status': 'current', 'as_of': DAY.isoformat(), 'claims': [claim()]}, DAY)
    assert passed['checks'][0]['outcome'] == 'passed'
    pending = merge_leadership(common, {'status': 'current', 'as_of': DAY.isoformat(), 'claims': []}, DAY)
    assert pending['checks'][0]['outcome'] == 'pending_verification'

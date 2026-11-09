"""严格行业地位来源、原文复读与合并边界。"""
import hashlib
import json
from datetime import date

from radar.stocks.leadership_source import load_leadership_source

DAY = date(2026, 11, 7)
QUOTE = '产业在线数据显示,美的中央空调国内市场销售额占比超过22%,继续保持行业第一'


def claim():
    return {'symbol': '000333', 'scope': '中央空调国内市场销售额', 'metric': 'share', 'value': '22%',
            'rank': 1, 'source_name': '产业在线', 'quote': QUOTE, 'report_date': '2026-06-30',
            'source_url': 'https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax?code=SZ000333'}


def write(tmp_path, value, raw_text=QUOTE):
    path = tmp_path / 'stock-leadership'; (path / 'raw').mkdir(parents=True, exist_ok=True)
    raw = json.dumps({'jyps': [{'REPORT_DATE': '2026-06-30 00:00:00', 'BUSINESS_REVIEW': raw_text}]},
                     ensure_ascii=False).encode()
    (path / 'raw' / '000333.json').write_bytes(raw)
    value['symbols']['000333'][0]['raw_file'] = 'raw/000333.json'
    value['symbols']['000333'][0]['raw_sha256'] = hashlib.sha256(raw).hexdigest()
    (path / 'leadership.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def test_leadership_source_reads_raw_and_accepts_top_three(tmp_path):
    write(tmp_path, {'as_of': DAY.isoformat(), 'symbols': {'000333': [claim()]}})
    result = load_leadership_source(tmp_path, '000333', DAY)
    assert result['status'] == 'current' and result['claims']


def test_leadership_source_rejects_arbitrary_url_or_rank(tmp_path):
    bad = claim(); bad['source_url'] = 'https://example.com/a'
    write(tmp_path, {'as_of': DAY.isoformat(), 'symbols': {'000333': [bad]}})
    assert load_leadership_source(tmp_path, '000333', DAY)['status'] == 'unavailable'

    bad = claim(); bad['rank'] = 100
    write(tmp_path, {'as_of': DAY.isoformat(), 'symbols': {'000333': [bad]}})
    assert load_leadership_source(tmp_path, '000333', DAY)['status'] == 'unavailable'


def test_leadership_source_rejects_quote_not_in_raw(tmp_path):
    bad = claim(); bad['quote'] = '公司自称第100名'
    write(tmp_path, {'as_of': DAY.isoformat(), 'symbols': {'000333': [bad]}}, raw_text=QUOTE)
    assert load_leadership_source(tmp_path, '000333', DAY)['status'] == 'unavailable'


def test_leadership_merge_pass_and_pending():
    from radar.stocks.formal_rules import merge_leadership

    common = {'symbol': '000333', 'checks': [{'code': 'leadership', 'outcome': 'pending_verification', 'evidence_ids': []}]}
    passed = merge_leadership(common, {'status': 'current', 'as_of': DAY.isoformat(), 'claims': [claim()]}, DAY)
    assert passed['checks'][0]['outcome'] == 'passed'
    pending = merge_leadership(common, {'status': 'current', 'as_of': DAY.isoformat(), 'claims': []}, DAY)
    assert pending['checks'][0]['outcome'] == 'pending_verification'

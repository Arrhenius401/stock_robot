"""严格行业地位来源：同一原文、同一公司、同一报告期、同一公告的绑定边界。"""
import hashlib
import json
from datetime import date

from radar.stocks.leadership_source import load_leadership_source

DAY = date(2026, 11, 7)
QUOTE = '产业在线数据显示,美的中央空调国内市场销售额占比超过22%,继续保持行业第一'
ART = 'AN202608280006758469'
TITLE = '美的集团:2026年半年度报告'


def claim():
    return {'symbol': '000333', 'scope': '中央空调国内市场销售额', 'metric': 'share', 'value': '超过22%',
            'rank': 1, 'source_name': '产业在线', 'quote': QUOTE, 'report_date': '2026-06-30',
            'source_url': 'https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax?code=SZ000333',
            'disclosed_on': '2026-08-29', 'announcement_title': TITLE,
            'announcement_url': f'https://data.eastmoney.com/notices/detail/000333/{ART}.html',
            'announcement_art_code': ART, 'disclosure_file': 'disclosures/000333.json',
            'announcement_raw_file': 'announcements/000333.json'}


def write(tmp_path, item, *, raw_rows=None, announcement_symbol='000333', announcement_title=TITLE,
          announcement_notice='2026-08-29 00:00:00', announcement_art=ART, disclosure_captured='2026-11-08T22:30:00+08:00'):
    root = tmp_path / 'stock-leadership'
    (root / 'raw').mkdir(parents=True, exist_ok=True)
    (root / 'disclosures').mkdir(exist_ok=True)
    (root / 'announcements').mkdir(exist_ok=True)
    rows = raw_rows or [{'SECURITY_CODE': '000333', 'REPORT_DATE': '2026-06-30 00:00:00',
                         'BUSINESS_REVIEW': item['quote']}]
    raw = json.dumps({'jyps': rows}, ensure_ascii=False).encode()
    (root / 'raw' / '000333.json').write_bytes(raw)
    item['raw_file'] = 'raw/000333.json'
    item['raw_sha256'] = hashlib.sha256(raw).hexdigest()
    item['announcement_title'] = announcement_title
    item['announcement_art_code'] = announcement_art
    item['announcement_url'] = f'https://data.eastmoney.com/notices/detail/{announcement_symbol}/{announcement_art}.html'
    disclosure = {'art_code': announcement_art, 'captured_at': disclosure_captured,
                  'notice_date': item['disclosed_on'], 'source_api': 'https://np-anotice-stock.eastmoney.com/api/security/ann',
                  'title': announcement_title, 'url': item['announcement_url']}
    disclosure_raw = json.dumps(disclosure, ensure_ascii=False).encode()
    (root / 'disclosures' / '000333.json').write_bytes(disclosure_raw)
    item['disclosure_sha256'] = hashlib.sha256(disclosure_raw).hexdigest()
    announcement = {'data': {'list': [{'art_code': announcement_art,
                                       'codes': [{'stock_code': announcement_symbol}],
                                       'notice_date': announcement_notice, 'title': announcement_title}]}}
    announcement_raw = json.dumps(announcement, ensure_ascii=False).encode()
    (root / 'announcements' / '000333.json').write_bytes(announcement_raw)
    item['announcement_raw_sha256'] = hashlib.sha256(announcement_raw).hexdigest()
    (root / 'leadership.json').write_text(
        json.dumps({'as_of': DAY.isoformat(), 'symbols': {'000333': [item]}}, ensure_ascii=False), encoding='utf-8')
    return root


def load(tmp_path):
    return load_leadership_source(tmp_path, '000333', DAY)


def test_leadership_source_reads_raw_and_disclosure(tmp_path):
    write(tmp_path, claim())
    result = load(tmp_path)
    assert result['status'] == 'current' and result['claims']


def test_leadership_source_rejects_arbitrary_url_or_rank(tmp_path):
    bad = claim(); bad['source_url'] = 'https://example.com/a'
    write(tmp_path, bad)
    assert load(tmp_path)['status'] == 'unavailable'

    bad = claim(); bad['rank'] = 100
    write(tmp_path, bad)
    assert load(tmp_path)['status'] == 'unavailable'


def test_claim_rank_must_match_quote(tmp_path):
    bad = claim(); bad['quote'] = '公司自称第100名'
    write(tmp_path, bad)
    assert load(tmp_path)['status'] == 'unavailable'


def test_disclosure_after_cutoff_is_unavailable(tmp_path):
    bad = claim(); bad['disclosed_on'] = '2026-11-08'
    write(tmp_path, bad)
    assert load(tmp_path)['status'] == 'unavailable'


def test_negated_top_three_is_unavailable(tmp_path):
    bad = claim(); bad['quote'] = '某协会数据显示国内市场未进入前三，排名第3名'
    bad['value'] = '前三'
    write(tmp_path, bad)
    assert load(tmp_path)['status'] == 'unavailable'


def test_other_company_rank_cannot_supply_missing_own_rank(tmp_path):
    bad = claim(); bad['quote'] = '某协会数据显示国内市场排名第一的是甲公司，乙公司排名第三'
    bad['value'] = '第三'
    bad['rank'] = 3
    write(tmp_path, bad)
    assert load(tmp_path)['status'] == 'unavailable'


def test_other_company_rank_in_following_clause_cannot_be_used(tmp_path):
    bad = claim()
    bad['quote'] = '某协会数据显示本公司国内市场占有率28%，甲公司排名第一'
    bad['value'] = '28%'
    bad['rank'] = 1
    write(tmp_path, bad)
    assert load(tmp_path)['status'] == 'unavailable'


def test_multiple_ranks_in_same_statement_are_ambiguous(tmp_path):
    bad = claim(); bad['quote'] = '某协会数据显示国内市场排名第三、排名第一'
    bad['value'] = '第三'
    bad['rank'] = 3
    write(tmp_path, bad)
    assert load(tmp_path)['status'] == 'unavailable'


def test_share_qualifier_must_be_written_into_value(tmp_path):
    bad = claim(); bad['value'] = '22%'
    write(tmp_path, bad)
    assert load(tmp_path)['status'] == 'unavailable'
    write(tmp_path, claim())
    assert load(tmp_path)['status'] == 'current'


def test_quote_must_come_from_claim_report_row(tmp_path):
    bad = claim()
    rows = [{'SECURITY_CODE': '000333', 'REPORT_DATE': '2025-12-31 00:00:00', 'BUSINESS_REVIEW': bad['quote']},
            {'SECURITY_CODE': '000333', 'REPORT_DATE': '2026-06-30 00:00:00', 'BUSINESS_REVIEW': '报告期无相关排名'}]
    write(tmp_path, bad, raw_rows=rows)
    assert load(tmp_path)['status'] == 'unavailable'


def test_quote_from_other_company_raw_row_is_unavailable(tmp_path):
    bad = claim()
    rows = [{'SECURITY_CODE': '000001', 'REPORT_DATE': '2026-06-30 00:00:00', 'BUSINESS_REVIEW': bad['quote']}]
    write(tmp_path, bad, raw_rows=rows)
    assert load(tmp_path)['status'] == 'unavailable'


def test_announcement_symbol_must_match_claim(tmp_path):
    bad = claim()
    write(tmp_path, bad, announcement_symbol='000001')
    assert load(tmp_path)['status'] == 'unavailable'


def test_announcement_notice_date_must_match_claim(tmp_path):
    bad = claim()
    write(tmp_path, bad, announcement_notice='2026-08-30 00:00:00')
    assert load(tmp_path)['status'] == 'unavailable'


def test_announcement_title_must_match_report_period(tmp_path):
    bad = claim()
    write(tmp_path, bad, announcement_title='美的集团:2026年年度报告')
    assert load(tmp_path)['status'] == 'unavailable'


def test_announcement_raw_sha_or_file_must_match(tmp_path):
    bad = claim()
    root = write(tmp_path, bad)
    (root / 'announcements/000333.json').write_bytes(b'{}')
    assert load(tmp_path)['status'] == 'unavailable'

    bad = claim(); bad['announcement_raw_file'] = 'announcements/000001.json'
    write(tmp_path, bad)
    assert load(tmp_path)['status'] == 'unavailable'


def test_source_name_must_appear_in_same_raw_row(tmp_path):
    bad = claim(); bad['source_name'] = '另一协会'
    rows = [{'SECURITY_CODE': '000333', 'REPORT_DATE': '2026-06-30 00:00:00',
             'BUSINESS_REVIEW': bad['quote'] + ' 另一协会另行统计'}]
    write(tmp_path, bad, raw_rows=[rows[0]])
    assert load(tmp_path)['status'] == 'current'
    rows = [{'SECURITY_CODE': '000333', 'REPORT_DATE': '2025-12-31 00:00:00',
             'BUSINESS_REVIEW': '另一协会另行统计'},
            {'SECURITY_CODE': '000333', 'REPORT_DATE': '2026-06-30 00:00:00', 'BUSINESS_REVIEW': bad['quote']}]
    write(tmp_path, bad, raw_rows=rows)
    assert load(tmp_path)['status'] == 'unavailable'


def test_leadership_merge_pass_and_pending():
    from radar.stocks.formal_rules import merge_leadership

    common = {'symbol': '000333', 'checks': [{'code': 'leadership', 'outcome': 'pending_verification', 'evidence_ids': []}]}
    passed = merge_leadership(common, {'status': 'current', 'symbol': '000333', 'as_of': DAY.isoformat(), 'claims': [claim()]}, DAY)
    assert passed['checks'][0]['outcome'] == 'passed'
    pending = merge_leadership(common, {'status': 'current', 'symbol': '000333', 'as_of': DAY.isoformat(), 'claims': []}, DAY)
    assert pending['checks'][0]['outcome'] == 'pending_verification'


def write_scale(tmp_path, count=6):
    from radar.stocks.industry_scale import build_industry_scale, save_industry_scale

    rows = [{'symbol': f'60000{index}', 'industry': '半导体', 'market_cap_yuan': str((count - index + 1) * 100),
             'market_cap_date': '2026-11-06', 'market_value_manifest_sha256': 'd' * 64}
            for index in range(1, count + 1)]
    source = {'universe': 'candidate_pool_626', 'scope_sha256': 'a' * 64,
              'industry_mapping_sha256': 'b' * 64, 'market_value_manifests_sha256': 'c' * 64}
    value = build_industry_scale(rows, as_of=DAY, source=source)
    save_industry_scale(tmp_path / 'stock-leadership' / 'scale.json', value)
    return value


def test_scale_top_three_passes_without_explicit_claim(tmp_path):
    write_scale(tmp_path)
    source = load_leadership_source(tmp_path, '600001', DAY)
    assert source['status'] == 'current' and source['claims'] == []
    assert source['scale']['rank'] == 1 and source['scale']['peer_count'] == 6
    from radar.stocks.formal_rules import merge_leadership

    common = {'symbol': '600001', 'checks': [{'code': 'leadership', 'outcome': 'pending_verification', 'evidence_ids': []}]}
    assert merge_leadership(common, source, DAY)['checks'][0]['outcome'] == 'passed'


def test_scale_rank_four_or_peer_four_stays_pending(tmp_path):
    from radar.stocks.formal_rules import merge_leadership

    write_scale(tmp_path)
    source = load_leadership_source(tmp_path, '600004', DAY)
    assert source['scale']['rank'] == 4
    common = {'symbol': '600004', 'checks': [{'code': 'leadership', 'outcome': 'pending_verification', 'evidence_ids': []}]}
    assert merge_leadership(common, source, DAY)['checks'][0]['outcome'] == 'pending_verification'

    write_scale(tmp_path, count=4)
    source = load_leadership_source(tmp_path, '600001', DAY)
    assert source['scale']['peer_count'] == 4
    assert merge_leadership(common, source, DAY)['checks'][0]['outcome'] == 'pending_verification'


def test_invalid_scale_does_not_block_explicit_claim(tmp_path):
    write(tmp_path, claim())
    (tmp_path / 'stock-leadership' / 'scale.json').write_text('{}', encoding='utf-8')
    source = load_leadership_source(tmp_path, '000333', DAY)
    assert source['status'] == 'current' and source['claims']
    from radar.stocks.formal_rules import merge_leadership

    common = {'symbol': '000333', 'checks': [{'code': 'leadership', 'outcome': 'pending_verification', 'evidence_ids': []}]}
    assert merge_leadership(common, source, DAY)['checks'][0]['outcome'] == 'passed'

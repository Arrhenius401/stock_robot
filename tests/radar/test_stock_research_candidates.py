"""研究候选摘要不改变资格或人工审核状态。"""
from copy import deepcopy


def test_research_summary_keeps_failed_unknown_and_unconfigured_separate():
    from radar.stocks.research_candidates import summarize_research

    item = {'symbol': '600900', 'as_of': '2026-11-03', 'evaluation_valid': True,
            'common_status': 'failed', 'checks': [
                {'code': 'listing_age', 'outcome': 'passed', 'reason': '满三年'},
                {'code': 'capitalization_current', 'outcome': 'failed', 'reason': '低于门槛'},
                {'code': 'audit', 'outcome': 'pending_verification', 'reason': '待核验'}],
            'pools': {'dividend_value': {'checks': [{'code': 'dividend_history', 'outcome': 'passed', 'reason': '三年'}],
                                           'unconfigured_rules': ['dividend_yield']}}}
    before = deepcopy(item)
    result = summarize_research(item, 'dividend_value')
    assert item == before
    assert [x['code'] for x in result['passed']] == ['listing_age', 'dividend_history']
    assert [x['code'] for x in result['failed']] == ['capitalization_current']
    assert [x['code'] for x in result['pending']] == ['audit']
    assert result['unconfigured_rules'] == ['dividend_yield']
    assert result['research_only'] and not result['formal_screening_enabled']


def test_invalid_result_cannot_look_like_confirmed_research():
    from radar.stocks.research_candidates import summarize_research

    result = summarize_research({'symbol': '600900', 'evaluation_valid': False, 'checks': [
        {'code': 'listing_age', 'outcome': 'passed', 'reason': '过期'}], 'pools': {}}, 'dividend_value')
    assert not result['passed'] and result['status'] == 'unavailable'


def test_audit_clues_preserve_year_source_and_never_claim_review():
    import hashlib
    import json

    from radar.stocks.research_candidates import extract_audit_clues

    rows = [{'SECURITY_CODE': '600900', 'REPORT_DATE': f'{year}-12-31 00:00:00',
             'NOTICE_DATE': f'{year+1}-04-30 00:00:00', 'UPDATE_DATE': f'{year+1}-04-30 00:00:00',
             'OPINION_TYPE': '标准无保留意见', 'OSOPINION_TYPE': None} for year in (2023, 2024, 2025)]
    raw = json.dumps({'data': rows}).encode()
    meta = {'source_url': 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/xjllbAjaxNew',
            'content_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
            'fetched_at': '2026-11-03T05:00:00+00:00'}
    result = extract_audit_clues('600900', '2026-11-03', raw, meta)
    assert [row['year'] for row in result['annual']] == [2023, 2024, 2025]
    assert all(row['opinion'] == '标准无保留意见' for row in result['annual'])
    assert result['source']['content_sha256'] == meta['content_sha256']
    assert result['reviewed'] is False and result['formal_screening_enabled'] is False
    assert extract_audit_clues('600900', '2026-11-03', raw+b' ', meta)['status'] == 'unavailable'


def test_audit_clue_rejects_conflicting_code_and_future_disclosure():
    import hashlib
    import json

    from radar.stocks.research_candidates import extract_audit_clues

    row = {'SECURITY_CODE': '600900', 'SECUCODE': '600900.SZ', 'REPORT_DATE': '2025-12-31',
           'NOTICE_DATE': '2026-04-30', 'UPDATE_DATE': '2026-04-30', 'OPINION_TYPE': '标准无保留意见'}
    def evaluate(value):
        raw = json.dumps({'data': [value]}).encode()
        meta = {'source_url': 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/xjllbAjaxNew',
                'content_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                'fetched_at': '2026-11-03T05:00:00+00:00'}
        return extract_audit_clues('600900', '2026-11-03', raw, meta)
    assert evaluate(row)['annual'][-1]['status'] == 'pending_verification'
    row.update(SECUCODE='600900.SH', NOTICE_DATE='2026-12-01')
    assert evaluate(row)['annual'][-1]['status'] == 'pending_verification'


def test_audit_clues_reject_source_error_even_if_data_present():
    import hashlib
    import json

    from radar.stocks.research_candidates import extract_audit_clues

    raw = json.dumps({'success': False, 'data': [{'SECURITY_CODE': '600900', 'REPORT_DATE': '2025-12-31',
        'NOTICE_DATE': '2026-04-30', 'UPDATE_DATE': '2026-04-30', 'OPINION_TYPE': '标准无保留意见'}]}).encode()
    meta = {'source_url': 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/xjllbAjaxNew',
            'content_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'fetched_at': '2026-11-03T05:00:00+00:00'}
    assert extract_audit_clues('600900', '2026-11-03', raw, meta)['status'] == 'unavailable'


def test_audit_opinion_classification_keeps_unknown_and_nonstandard_separate():
    from radar.stocks.research_candidates import classify_audit_clues

    base = {'status': 'available', 'annual': [
        {'year': year, 'status': 'available', 'opinion': '标准无保留意见', 'updated_after_cutoff': False}
        for year in (2023, 2024, 2025)]}
    assert classify_audit_clues(base, '2026-11-03')['status'] == 'standard_opinions'
    changed = deepcopy(base)
    changed['annual'][1]['opinion'] = '保留意见'
    assert classify_audit_clues(changed, '2026-11-03')['status'] == 'serious_opinion'
    changed['annual'][2]['status'] = 'pending_verification'
    assert classify_audit_clues(changed, '2026-11-03')['status'] == 'pending_verification'
    changed = deepcopy(base)
    changed['annual'][0]['updated_after_cutoff'] = True
    assert classify_audit_clues(changed, '2026-11-03')['status'] == 'pending_verification'
    changed = deepcopy(base)
    changed['annual'][0]['year'] = 2024
    assert classify_audit_clues(changed, '2026-11-03')['status'] == 'pending_verification'
    changed = deepcopy(base)
    changed['status'] = 'historical'
    assert classify_audit_clues(changed, '2026-11-03')['status'] == 'pending_verification'


def test_audit_raw_rejects_duplicate_keys_and_inconsistent_update_dates():
    import hashlib
    import json

    from radar.stocks.research_candidates import extract_audit_clues

    def evaluate(raw):
        meta = {'source_url': 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/xjllbAjaxNew',
                'content_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                'fetched_at': '2026-11-03T05:00:00+00:00'}
        return extract_audit_clues('600900', '2026-11-03', raw, meta)
    duplicate = b'{"data":[],"data":[]}'
    assert evaluate(duplicate)['status'] == 'unavailable'
    row = {'SECURITY_CODE': '600900', 'REPORT_DATE': '2025-12-31', 'NOTICE_DATE': '2026-04-30',
           'UPDATE_DATE': '2026-04-29', 'OPINION_TYPE': '标准无保留意见'}
    assert evaluate(json.dumps({'data': [row]}).encode())['annual'][-1]['status'] == 'pending_verification'
    row['UPDATE_DATE'] = '2026-11-04'
    assert evaluate(json.dumps({'data': [row]}).encode())['annual'][-1]['status'] == 'pending_verification'


def test_missing_other_audit_opinion_field_is_unknown():
    import hashlib
    import json

    from radar.stocks.research_candidates import extract_audit_clues

    row = {'SECURITY_CODE': '600900', 'SECUCODE': '600900.SH', 'REPORT_DATE': '2025-12-31',
           'NOTICE_DATE': '2026-04-30', 'UPDATE_DATE': '2026-04-30', 'OPINION_TYPE': '标准无保留意见'}
    raw = json.dumps({'data': [row]}).encode()
    meta = {'source_url': 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/xjllbAjaxNew',
            'content_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'fetched_at': '2026-11-03T05:00:00+00:00'}
    result = extract_audit_clues('600900', '2026-11-03', raw, meta)
    assert result['annual'][-1]['status'] == 'pending_verification'


def test_audit_deeply_nested_original_is_unavailable():
    import hashlib

    from radar.stocks.research_candidates import extract_audit_clues

    raw = b'[' * 2000 + b']' * 2000
    meta = {'source_url': 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/xjllbAjaxNew',
            'content_sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'fetched_at': '2026-11-03T05:00:00+00:00'}
    assert extract_audit_clues('600900', '2026-11-03', raw, meta)['status'] == 'unavailable'

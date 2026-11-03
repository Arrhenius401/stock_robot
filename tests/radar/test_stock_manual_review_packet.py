"""按需审核包只投影已有事实，不自动审核或放行。"""
from copy import deepcopy

import pytest


def detail():
    checks = [{'code': code, 'outcome': 'pending_verification', 'reason': '缺少审核', 'evidence_ids': []}
              for code in ('security', 'audit', 'governance', 'leadership', 'financial_quality')]
    return {'company': {'symbol': '600900', 'name': '合成公司', 'revision': 1},
            'evidence': [{'evidence_id': 'e1', 'kind': 'admission_audit', 'review_status': 'pending',
                          'source_url': 'https://example.org/a', 'content_sha256': 'a'*64,
                          'disclosed_on': '2026-01-01', 'payload': {'basis': '线索'}}],
            'reviews': [], 'evaluation': {'as_of': '2026-11-02', 'items': [{
                'symbol': '600900', 'evaluation_valid': True, 'checks': checks,
                'pools': {'dividend_value': {'checks': [{'code': 'cash_review', 'outcome': 'pending_verification',
                                                        'reason': '待核验', 'evidence_ids': []}]}},
                'financial_source': {'status': 'historical', 'as_of': '2026-10-18', 'financial': {'cash_flow_reference': {'ratio': '0.9'}}},
                'industry_source': {'status': 'local_reference', 'industry': {'level1': '电力'}},
                'company_profile_source': {'status': 'unavailable'}}]}}


def test_packet_is_single_company_and_only_preserves_existing_status():
    from radar.stocks.manual_review_packet import build_manual_review_packet

    original = detail()
    before = deepcopy(original)
    packet = build_manual_review_packet(original)
    assert original == before
    assert packet['symbol'] == '600900' and len(packet['checks']) == 6
    assert all(row['outcome'] == 'pending_verification' for row in packet['checks'])
    assert packet['sources']['financial']['status'] == 'historical'
    assert packet['sources']['industry']['status'] == 'local_reference'
    assert packet['evidence'][0]['review_status'] == 'pending'
    assert packet['formal_screening_enabled'] is False and packet['members_published'] == 0


def test_conflicting_review_and_local_industry_remain_unresolved():
    from radar.stocks.manual_review_packet import build_manual_review_packet

    source = detail()
    leadership = next(row for row in source['evaluation']['items'][0]['checks'] if row['code'] == 'leadership')
    leadership.update(reason='同组已审核事实存在冲突，须重新核验', evidence_ids=['a', 'b'])
    packet = build_manual_review_packet(source)
    result = next(row for row in packet['checks'] if row['code'] == 'leadership')
    assert result['outcome'] == 'pending_verification' and result['evidence_ids'] == ['a', 'b']
    assert packet['sources']['industry']['status'] == 'local_reference'


@pytest.mark.parametrize('change', ['missing_company', 'mismatched_symbol', 'failed_evaluation'])
def test_inconsistent_detail_cannot_produce_a_review_packet(change):
    from radar.stocks.manual_review_packet import build_manual_review_packet

    source = detail()
    if change == 'missing_company': source['company'] = None
    elif change == 'mismatched_symbol': source['evaluation']['items'][0]['symbol'] = '600036'
    else: source['evaluation']['items'][0]['evaluation_valid'] = False
    with pytest.raises((ValueError, TypeError)):
        build_manual_review_packet(source)


def test_packet_includes_only_matching_unreviewed_audit_clues():
    from radar.stocks.manual_review_packet import build_manual_review_packet

    source = detail()
    source['audit_clues'] = {'symbol': '600900', 'as_of': '2026-11-02', 'status': 'available',
                             'reviewed': False, 'formal_screening_enabled': False,
                             'annual': [{'year': 2025, 'status': 'available', 'opinion': '标准无保留意见',
                                         'disclosed_on': '2026-04-30'}],
                             'source': {'source_url': 'https://example.org/source', 'content_sha256': 'a'*64,
                                        'fetched_at': '2026-11-02T00:00:00+00:00'}}
    packet = build_manual_review_packet(source)
    assert packet['audit_clues'] == source['audit_clues']
    packet['audit_clues']['annual'][0]['opinion'] = 'modified'
    assert source['audit_clues']['annual'][0]['opinion'] == '标准无保留意见'
    assert all(row['outcome'] == 'pending_verification' for row in packet['checks'])


@pytest.mark.parametrize('patch', [
    {'symbol': '600036'}, {'as_of': '2026-11-01'}, {'reviewed': True},
    {'formal_screening_enabled': True}, {'status': 'invalid'},
])
def test_packet_rejects_unbound_or_untrusted_audit_clues(patch):
    from radar.stocks.manual_review_packet import build_manual_review_packet

    source = detail()
    source['audit_clues'] = {'symbol': '600900', 'as_of': '2026-11-02', 'status': 'available',
                             'reviewed': False, 'formal_screening_enabled': False, 'annual': [], **patch}
    assert build_manual_review_packet(source)['audit_clues']['status'] == 'unavailable'

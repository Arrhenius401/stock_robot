import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from radar.stocks.announcement_discovery import discover_announcements


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status_code = status

    def json(self):
        return self.data

    def close(self):
        return None


def row(identifier='1', **changes):
    value = {'secCode': '600900', 'orgId': 'org1', 'announcementId': identifier,
             'announcementTitle': '2025年年度<em>权益</em>分派实施公告',
             'announcementTime': int(datetime(2026, 7, 9, 16, tzinfo=UTC).timestamp() * 1000),
             'adjunctUrl': f'finalpage/2026-07-10/{identifier}.PDF'}
    value.update(changes)
    return value


def page(rows, total=None, more=False):
    return {'totalAnnouncement': len(rows) if total is None else total,
            'totalRecordNum': len(rows) if total is None else total,
            'announcements': rows, 'hasMore': more, 'totalpages': 0}


def run(tmp_path, answer, **kwargs):
    calls = []

    def post(url, **options):
        calls.append(options['data'])
        if 'topSearch' in url:
            return Response([{'code': '600900', 'category': 'A股', 'orgId': 'org1', 'type': 'shj'}])
        return answer(options['data'])

    result = discover_announcements([{'symbol': '600900', 'name': '长江电力'}], date(2026, 10, 14),
                                    tmp_path, http_post=post, sleep=lambda _: None, **kwargs)
    return result, calls


def test_complete_dedup_and_cache(tmp_path):
    result, calls = run(tmp_path, lambda _: Response(page([row()])))
    assert result['companies'][0]['status'] == 'query_scope_complete'
    assert len(result['documents']) == 1
    assert result['documents'][0]['title'] == '2025年年度权益分派实施公告'
    assert result['documents'][0]['disclosed_on'] == '2026-07-10'
    assert result['rule_enabled'] is False
    assert result['financial_verified'] is False
    assert len(calls) == 3
    result, calls = run(tmp_path, lambda _: pytest.fail('cache should be reused'))
    assert calls == []
    assert result['companies'][0]['status'] == 'query_scope_complete'


@pytest.mark.parametrize('change', [
    {'secCode': '000001'}, {'orgId': 'wrong'},
    {'adjunctUrl': '../secret.PDF'}, {'adjunctUrl': 'https://example.com/a.PDF'},
    {'adjunctUrl': 'finalpage/%2e%2e/a.PDF'},
    {'announcementTime': int(datetime(2027, 1, 1, tzinfo=UTC).timestamp() * 1000)},
])
def test_bad_identity_url_or_future_is_incomplete(tmp_path, change):
    result, _ = run(tmp_path, lambda _: Response(page([row(**change)])))
    assert result['companies'][0]['status'] == 'failed'
    assert result['documents'] == []


def test_pagination_total_and_duplicate_failure(tmp_path):
    result, _ = run(tmp_path, lambda body: Response(page([row()], total=2, more=body['pageNum'] == 1)))
    assert result['companies'][0]['status'] == 'failed'


def test_pagination_success(tmp_path):
    result, _ = run(tmp_path, lambda body: Response(page([row(str(body['pageNum']))], total=2,
                                                        more=body['pageNum'] == 1)))
    assert len(result['documents']) == 2
    assert result['companies'][0]['status'] == 'query_scope_complete'


def test_zero_is_only_query_coverage(tmp_path):
    result, _ = run(tmp_path, lambda _: Response(page([])))
    assert result['companies'][0]['status'] == 'query_scope_complete'
    assert result['documents'] == []
    assert result['companies'][0]['annual_coverage_verified'] is False


def test_429_stops_origin(tmp_path):
    result, calls = run(tmp_path, lambda _: Response({}, status=429))
    assert len(calls) == 2
    assert result['companies'][0]['status'] == 'failed'
    assert result['source_stopped'] is True


def test_correction_kept_pending(tmp_path):
    result, _ = run(tmp_path, lambda _: Response(page([row(announcementTitle='权益分派实施公告更正公告')])))
    assert result['documents'][0]['status'] == 'pending_review'


def test_ambiguous_lookup(tmp_path):
    def post(*args, **kwargs):
        return Response([{'code': '600900', 'category': 'A股', 'orgId': value} for value in ['a', 'b']])

    result = discover_announcements([{'symbol': '600900'}], date(2026, 10, 14), tmp_path,
                                    http_post=post, sleep=lambda _: None)
    assert result['companies'][0]['status'] == 'failed'


def test_invalid_symbol_no_network(tmp_path):
    with pytest.raises(ValueError):
        discover_announcements([{'symbol': '../x'}], date(2026, 10, 14), tmp_path)


def test_changing_total_is_incomplete(tmp_path):
    result, _ = run(tmp_path, lambda body: Response(page([row(str(body['pageNum']))],
                                                        total=2 if body['pageNum'] == 1 else 3,
                                                        more=body['pageNum'] == 1)))
    assert result['companies'][0]['status'] == 'failed'
    assert result['documents'][0]['query_scope_complete'] is False


def test_missing_schema_not_empty_success(tmp_path):
    result, _ = run(tmp_path, lambda _: Response({'announcements': []}))
    assert result['companies'][0]['status'] == 'failed'


def test_retry_5xx_budget(tmp_path):
    result, calls = run(tmp_path, lambda _: Response({}, status=503))
    assert len(calls) == 4  # 身份查询一次，失败公告查询最多三次。
    assert result['companies'][0]['status'] == 'failed'


def test_network_retry_and_other_company_isolation(tmp_path):
    import requests

    count = 0

    def post(*args, **kwargs):
        nonlocal count
        count += 1
        raise requests.ConnectionError('offline')

    result = discover_announcements([{'symbol': '600900'}, {'symbol': '000333'}], date(2026, 10, 14),
                                    tmp_path, http_post=post, sleep=lambda _: None)
    assert count == 6
    assert all(company['status'] == 'failed' for company in result['companies'])


def test_conflicting_terms_pending(tmp_path):
    result, _ = run(tmp_path, lambda body: Response(page([row(announcementTitle=
                       ('权益分派实施公告' if body['searchkey'] == '权益分派' else '利润分配实施公告'))])))
    assert result['companies'][0]['status'] == 'failed'
    assert result['documents'][0]['status'] == 'pending_review'


def test_refresh_and_corrupt_cache(tmp_path):
    run(tmp_path, lambda _: Response(page([])))
    result, calls = run(tmp_path, lambda _: Response(page([])), refresh=True)
    assert len(calls) == 3
    for path in tmp_path.glob('*.json'):
        path.write_text('broken', encoding='utf-8')
    result, calls = run(tmp_path, lambda _: Response(page([])))
    assert len(calls) == 3
    assert result['companies'][0]['status'] == 'query_scope_complete'


def test_html_challenge_not_retried(tmp_path):
    class HtmlResponse(Response):
        def json(self):
            raise ValueError('HTML')

    result, calls = run(tmp_path, lambda _: HtmlResponse(None))
    assert len(calls) == 2
    assert result['companies'][0]['status'] == 'failed'


def test_encoded_title_markup_is_removed(tmp_path):
    result, _ = run(tmp_path, lambda _: Response(page([row(announcementTitle='2025年度&lt;em&gt;权益&lt;/em&gt;分派实施公告')])))
    assert result['documents'][0]['title'] == '2025年度权益分派实施公告'


def test_capture_metadata_and_diagnostics(tmp_path):
    result, _ = run(tmp_path, lambda _: Response(page([])))
    capture = result['companies'][0]['query_states'][0]['raw_pages'][0]
    assert datetime.fromisoformat(capture['captured_at']).utcoffset() is not None
    assert capture['cache_reused'] is False
    result, _ = run(tmp_path, lambda _: Response(page([])))
    assert result['companies'][0]['query_states'][0]['raw_pages'][0]['cache_reused'] is True
    result, _ = run(tmp_path, lambda _: Response({}, status=429), refresh=True)
    assert result['diagnostics'][0]['error'].startswith('来源返回 429')


def test_boolean_total_record_count_rejected(tmp_path):
    payload = page([])
    payload['totalRecordNum'] = False
    result, _ = run(tmp_path, lambda _: Response(payload))
    assert result['companies'][0]['status'] == 'failed'


def test_real_cninfo_null_empty_response(tmp_path):
    sample = Path(__file__).parents[1] / 'resources/radar/cninfo-000333-announcements-empty.json'
    payload = json.loads(sample.read_text(encoding='utf-8'))
    result, _ = run(tmp_path, lambda _: Response(payload))
    assert result['companies'][0]['status'] == 'query_scope_complete'
    assert result['documents'] == []
    assert result['companies'][0]['annual_coverage_verified'] is False


@pytest.mark.parametrize('payload', [
    {'totalAnnouncement': 0, 'totalRecordNum': 0, 'hasMore': False},
    {'totalAnnouncement': 1, 'totalRecordNum': 1, 'announcements': None, 'hasMore': False},
    {'totalAnnouncement': 0, 'totalRecordNum': 0, 'announcements': None, 'hasMore': True},
])
def test_null_empty_requires_all_explicit_fields(tmp_path, payload):
    result, _ = run(tmp_path, lambda _: Response(payload))
    assert result['companies'][0]['status'] == 'failed'


def test_discovery_to_extraction_date_and_review_contract(tmp_path):
    from radar.stocks.dividend_extract import extract_archived_documents

    result, _ = run(tmp_path, lambda _: Response(page([row(announcementTitle='权益分派实施公告更正公告')])))
    document = result['documents'][0]
    assert document['disclosed_on'] == '2026-07-10'
    assert document['discovery_status'] == 'pending_review'
    # 下载状态改变后，更正公告的独立审核标记仍阻止模板提取。
    archived = {**document, 'status': 'downloaded'}
    extracted = extract_archived_documents({'items': [archived]}, tmp_path, date(2026, 10, 14))
    assert extracted['items'][0]['disclosed_on'] == document['disclosed_on']
    assert '公告发现结果尚待人工确认' in extracted['items'][0]['reasons']
    assert extracted['event_proposals'] == []


@pytest.mark.parametrize('title', ['取消2025年度利润分配方案公告', '终止权益分派公告',
                                  '2025年度利润分配调整公告', '权益分派更正公告'])
def test_related_non_implementation_risk_kept_pending(tmp_path, title):
    from radar.stocks.dividend_extract import extract_archived_documents

    result, _ = run(tmp_path, lambda _: Response(page([row(announcementTitle=title)])))
    document = result['documents'][0]
    assert document['kind'] == 'related_review'
    assert document['discovery_status'] == 'pending_review'
    assert document['financial_verified'] is False
    assert result['companies'][0]['annual_coverage_verified'] is False
    extracted = extract_archived_documents({'items': [{**document, 'status': 'downloaded'}]},
                                          tmp_path, date(2026, 10, 14))
    assert extracted['event_proposals'] == []
    assert '公告发现结果尚待人工确认' in extracted['items'][0]['reasons']


def test_unrelated_correction_not_collected(tmp_path):
    result, _ = run(tmp_path, lambda _: Response(page([row(announcementTitle='董事会会议更正公告')])))
    assert result['documents'] == []


def test_annual_report_mode_filters_summaries_and_retains_revisions(tmp_path):
    rows = [row('1', announcementTitle='2025年年度报告'),
            row('2', announcementTitle='2025年年度报告摘要'),
            row('3', announcementTitle='2025年年度报告（修订版）'),
            row('4', announcementTitle='2025年年度报告（英文版）')]
    result, calls = run(tmp_path, lambda _: Response(page(rows)), mode='annual_report')
    assert len(calls) == 2
    assert [d['announcement_id'] for d in result['documents']] == ['1', '3']
    assert result['documents'][0]['kind'] == 'annual_report'
    assert result['documents'][0]['fiscal_year'] == 2025
    assert result['documents'][1]['discovery_status'] == 'pending_review'
    assert result['companies'][0]['query_states'][0]['term'] == '年度报告'


def test_unknown_discovery_mode_rejected_before_network(tmp_path):
    with pytest.raises(ValueError, match='发现模式'):
        run(tmp_path, lambda _: pytest.fail('must not request'), mode='unknown')


def test_report_related_title_is_not_mistaken_for_full_annual_report(tmp_path):
    rows = [row('1', announcementTitle='关于2025年年度报告披露日期变更的公告'),
            row('2', announcementTitle='关于2025年年度报告的审议意见'),
            row('3', announcementTitle='2025年年度报告（全文）')]
    result, _ = run(tmp_path, lambda _: Response(page(rows)), mode='annual_report')
    assert [d['announcement_id'] for d in result['documents']] == ['1', '3']
    assert result['documents'][0]['discovery_status'] == 'pending_review'
    assert result['documents'][1]['discovery_status'] == 'discovered'

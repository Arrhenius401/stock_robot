"""交易所信息披露评价正向依据与治理合并边界。"""
import hashlib
import json
from datetime import date

from radar.stocks.disclosure_evaluation import load_disclosure_evaluation
from radar.stocks.formal_rules import merge_governance

DAY = date(2026, 11, 7)
TODAY = DAY.isoformat()


def evaluation(grade='A', **overrides):
    payload = {'version': 'exchange-disclosure-evaluation-v1',
               'period_start': '2024-07-01', 'period_end': '2025-06-30', 'published_on': '2025-10-31',
               'sources': [{'exchange': 'SSE', 'title': '沪市上市公司信息披露工作评价结果',
                            'notice_url': 'https://www.sse.com.cn/regulation/supervision/dynamic/c/c_20251024_10795890.shtml',
                            'pdf_url': 'https://www.sse.com.cn/regulation/supervision/dynamic/c/10795890/files/a.pdf',
                            'published_on': '2025-10-24', 'sha256': 'a' * 64, 'bytes': 100}],
               'grades': {f'{index:06d}': 'A' for index in range(1000)}}
    payload['grades']['600000'] = grade
    payload.update(overrides)
    return payload


def write(tmp_path, payload):
    path = tmp_path / 'evaluation.json'
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return path


def source(result='current', evaluation_value=None):
    return {'status': result, 'symbol': '600000', 'as_of': TODAY, 'records': [],
            'evaluation': evaluation_value or {'status': 'current', 'grade': 'A'}}


def check(result):
    return next(row for row in result['checks'] if row['code'] == 'governance')


def common():
    return {'symbol': '600000', 'checks': [{'code': 'governance', 'outcome': 'pending_verification', 'evidence_ids': []}]}


def test_loader_reads_grade_and_source_metadata(tmp_path):
    path = write(tmp_path, evaluation())
    result = load_disclosure_evaluation('600000', DAY, path=path)
    assert result['status'] == 'current' and result['grade'] == 'A'
    assert result['period_end'] == '2025-06-30' and result['published_on'] == '2025-10-31'
    assert result['file_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_loader_missing_grade_is_current_but_no_grade(tmp_path):
    path = write(tmp_path, evaluation())
    result = load_disclosure_evaluation('600001', DAY, path=path)
    assert result['status'] == 'current' and result['grade'] is None


def test_loader_rejects_not_yet_published_or_bad_source(tmp_path):
    path = write(tmp_path, evaluation(published_on='2026-11-08'))
    assert load_disclosure_evaluation('600000', DAY, path=path)['status'] == 'unavailable'

    bad = evaluation()
    bad['sources'][0]['pdf_url'] = 'https://example.com/a.pdf'
    path = write(tmp_path, bad)
    assert load_disclosure_evaluation('600000', DAY, path=path)['status'] == 'unavailable'

    bad = evaluation()
    bad['sources'][0]['sha256'] = 'bad'
    path = write(tmp_path, bad)
    assert load_disclosure_evaluation('600000', DAY, path=path)['status'] == 'unavailable'


def test_ab_grade_passes_when_no_discipline_or_measure():
    assert check(merge_governance(common(), source(), DAY))['outcome'] == 'passed'
    assert check(merge_governance(common(), source(evaluation_value={'status': 'current', 'grade': 'B'}), DAY))['outcome'] == 'passed'


def test_cd_or_missing_grade_stays_pending():
    assert check(merge_governance(common(), source(evaluation_value={'status': 'current', 'grade': 'C'}), DAY))['outcome'] == 'pending_verification'
    assert check(merge_governance(common(), source(evaluation_value={'status': 'current', 'grade': None}), DAY))['outcome'] == 'pending_verification'
    assert check(merge_governance(common(), source(evaluation_value={'status': 'unavailable', 'grade': None}), DAY))['outcome'] == 'pending_verification'


def test_hard_failure_and_measure_take_priority_over_ab():
    failed = source()
    failed['records'] = [{'listed_on': '2025-01-01', 'measure_category': 'discipline', 'object': '上市公司',
                          'title': '纪律处分决定'}]
    assert check(merge_governance(common(), failed, DAY))['outcome'] == 'failed'

    measure = source()
    measure['records'] = [{'listed_on': '2025-01-01', 'measure_category': 'regulatory_measure', 'object': '上市公司',
                           'title': '监管警示'}]
    assert check(merge_governance(common(), measure, DAY))['outcome'] == 'pending_verification'

"""发行人类型分类的保守边界。"""
import json
from copy import deepcopy
from datetime import date

import pytest

from radar.stocks.issuer_type import classify_issuer

SOURCES = {
    'banking': {'url': 'https://www.nfra.gov.cn/chinese/docfile/2025/bank.pdf',
                'title': '银行业金融机构法人名单', 'as_of': '2025-06-30'},
    'insurance': {'url': 'https://www.nfra.gov.cn/chinese/docfile/2025/insurance.pdf',
                  'title': '保险机构法人名单', 'as_of': '2025-06-30'},
    'financial_holding': {'url': 'https://www.nfra.gov.cn/chinese/docfile/2025/holding.pdf',
                          'title': '金融控股公司法人名单', 'as_of': '2025-06-30'},
    'securities': {'url': 'https://www.csrc.gov.cn/csrc/c101860/c1029659/content.shtml',
                   'title': '证券公司名录（2026年9月）', 'as_of': '2026-09'},
}


def payload():
    return {'as_of': '2025-06-30', 'sources': deepcopy(SOURCES),
            'names': {'bank': ['平安银行股份有限公司'], 'insurance': ['示例保险股份有限公司'],
                      'securities': ['示例证券股份有限公司'], 'other_financial': ['示例信托股份有限公司']}}


def write_roster(tmp_path, monkeypatch, value):
    from radar.stocks import issuer_type

    path = tmp_path / 'rosters.json'
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(issuer_type, 'ROSTER_PATH', path)
    return path


def test_positive_license_matches():
    assert classify_issuer('平安银行股份有限公司', 'J 金融业')['issuer_type'] == 'bank'
    assert classify_issuer('中国平安保险(集团)股份有限公司', 'J 金融业')['issuer_type'] == 'insurance'
    assert classify_issuer('中信证券股份有限公司', 'J 金融业')['issuer_type'] == 'securities'
    assert classify_issuer('建元信托股份有限公司', 'J 金融业')['issuer_type'] == 'other_financial'


def test_ordinary_requires_non_financial_industry_and_name():
    result = classify_issuer('合成制造股份有限公司', 'C 制造业')
    assert result['issuer_type'] == 'ordinary' and result['status'] == 'verified'
    assert classify_issuer('上海陆家嘴金融贸易区开发股份有限公司', '房地产')['issuer_type'] == 'ordinary'
    assert classify_issuer('中油资本股份有限公司', 'J 金融业')['status'] == 'unclassified_financial'
    assert classify_issuer('渤海租赁股份有限公司', 'L 商务服务')['status'] == 'unclassified_financial'
    assert classify_issuer('东方财富信息股份有限公司', 'J 金融业')['status'] == 'unclassified_financial'
    assert classify_issuer('', 'C 制造业')['status'] == 'pending_verification'


def test_valid_roster_still_classifies(tmp_path, monkeypatch):
    write_roster(tmp_path, monkeypatch, payload())
    result = classify_issuer('平安银行股份有限公司', '')
    assert result['issuer_type'] == 'bank' and result['status'] == 'verified'
    assert result['source']['file_sha256']
    assert result['source']['securities']['as_of'] == '2026-09'


@pytest.mark.parametrize('mutation', ['no_sources', 'no_as_of', 'missing_source', 'bad_title',
                                      'bad_date', 'month_date', 'bank_month', 'bad_url', 'non_official_url', 'empty_names'])
def test_missing_or_invalid_roster_metadata_is_unavailable(tmp_path, monkeypatch, mutation):
    value = payload()
    if mutation == 'no_sources':
        del value['sources']
    elif mutation == 'no_as_of':
        del value['as_of']
    elif mutation == 'missing_source':
        del value['sources']['securities']
    elif mutation == 'bad_title':
        value['sources']['banking']['title'] = ''
    elif mutation == 'bad_date':
        value['sources']['banking']['as_of'] = '2025-13-01'
    elif mutation == 'month_date':
        value['as_of'] = '2025-06'
    elif mutation == 'bad_url':
        value['sources']['securities']['url'] = ''
    elif mutation == 'non_official_url':
        value['sources']['securities']['url'] = 'https://example.com/securities'
    else:
        value['names']['bank'] = []
    write_roster(tmp_path, monkeypatch, value)
    result = classify_issuer('平安银行股份有限公司', 'J 金融业')
    assert result['issuer_type'] is None
    assert result['status'] == 'unavailable'


def test_metadata_change_after_first_read_invalidates(tmp_path, monkeypatch):
    path = write_roster(tmp_path, monkeypatch, payload())
    assert classify_issuer('平安银行股份有限公司', '')['status'] == 'verified'
    broken = payload()
    del broken['sources']
    path.write_text(json.dumps(broken, ensure_ascii=False), encoding='utf-8')
    assert classify_issuer('平安银行股份有限公司', '')['status'] == 'unavailable'


def test_unverified_issuer_does_not_pass_security_condition():
    from radar.stocks.admission import evaluate_common_admission
    from radar.stocks.company_profile_admission import merge_common_profile

    day = date(2026, 11, 7)
    manual = evaluate_common_admission('600000', [], day)
    source = {'symbol': '600000', 'evaluation_as_of': day.isoformat(), 'status': 'current',
              'as_of': day.isoformat(), 'checks': [],
              'security_market': {'symbol': '600000', 'status': 'verified', 'exchange': 'SSE',
                                  'security_type': 'ordinary_a', 'issuer_type': None,
                                  'issuer_type_status': 'unavailable'}}
    result = merge_common_profile(manual, source, day)
    security = next(row for row in result['checks'] if row['code'] == 'security')
    assert security['outcome'] == 'pending_verification'

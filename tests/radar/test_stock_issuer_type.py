"""发行人类型分类的保守边界。"""
from radar.stocks.issuer_type import classify_issuer


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

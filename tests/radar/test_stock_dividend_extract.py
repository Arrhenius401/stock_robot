from datetime import date

from radar.stocks.dividend_extract import extract_dividend_text


def test_plain_template_and_exact_money():
    result = extract_dividend_text(SIMPLE, symbol='600123', source_url=URL,
                                   disclosed_on='2026-07-01', as_of=date(2026, 10, 14))
    assert result['status'] == 'matched_template'
    row = result['event_proposals'][0]
    assert row['amount_yuan'] == '123456789.12'
    assert row['paid_on'] == '2026-07-17'
    assert row['fiscal_year'] == 2025
    assert row['financial_verified'] is False
    assert result['requires_review'] is True
    assert 'events' not in result and 'coverage' not in result


URL = 'https://example.com/dividend.pdf'
SIMPLE = '''证券代码：600123 证券简称：测试公司
2025年年度权益分派实施公告
发放年度：2025年年度
3.分配方案：本次利润分配以公司总股本100,000,000股为基数，
每股派发现金红利1.2345678912元（含税），共计派发现金红利123,456,789.12元。
三、相关日期
股份类别 股权登记日 最后交易日 除权（息）日 现金红利发放日
Ａ股 2026/7/16 － 2026/7/17 2026/7/17
四、分配实施办法
'''

from hashlib import sha256
from pathlib import Path

import pytest

from radar.stocks.dividend_extract import extract_archived_documents

RESOURCES = Path(__file__).parents[1] / 'resources' / 'radar'


def extract(text=SIMPLE, **kwargs):
    return extract_dividend_text(text, symbol=kwargs.get('symbol', '600123'), source_url=URL,
                                 disclosed_on=kwargs.get('disclosed_on', '2026-07-01'),
                                 as_of=date(2026, 10, 14))


@pytest.mark.parametrize(('filename', 'symbol', 'amount', 'paid'), [
    ('cypc-600900-2023-dividend-implementation.txt', '600900', '20063938527.12', '2024-07-19'),
    ('cypc-600900-2024-dividend-implementation.txt', '600900', '17935203585.83', '2025-07-18'),
    ('cypc-600900-2025-dividend-implementation.txt', '600900', '19329891995.64', '2026-07-17'),
    ('hkex-000333-2025-a-dividend-implementation.txt', '000333', '25916074233', '2026-06-29'),
    ('cninfo-601088-2025-dividend-implementation.txt', '601088', '18861310873.12', '2026-07-13'),
])
def test_actual_clipped_documents(filename, symbol, amount, paid):
    result = extract((RESOURCES / filename).read_text(encoding='utf-8'), symbol=symbol)
    assert result['status'] == 'matched_template', result['reasons']
    current = result['event_proposals'][0]
    assert current['amount_yuan'] == amount
    assert current['paid_on'] == paid
    assert current['share_class'] == 'A'
    assert current['financial_verified'] is False
    if symbol == '000333':
        assert current['eligible_shares'] == '6820019535'
        assert current['per_share_yuan'] == '3.8'
        assert all(row['amount_yuan'] != '28586881851' for row in result['event_proposals'])
    if filename.startswith('cypc') and '2023' not in filename:
        annual = result['event_proposals'][1]
        assert annual['amount_scope'] == 'fiscal_year_total'
        assert annual['paid_on'] is None
        assert annual['status'] == 'pending_verification'
    if '2023' in filename:
        assert len(result['event_proposals']) == 1


@pytest.mark.parametrize(('old', 'new'), [
    ('123,456,789.12元', '12345.678912万元'),
    ('123,456,789.12元', '123.45678912百万元'),
])
def test_explicit_units(old, new):
    assert extract(SIMPLE.replace(old, new))['event_proposals'][0]['amount_yuan'] == '123456789.12'


@pytest.mark.parametrize('text', [
    SIMPLE.replace('600123', '600124'),
    SIMPLE.replace('实施公告', '预案'),
    SIMPLE.replace('2025年年度权益', '取消2025年年度权益'),
    SIMPLE.replace('共计派发现金红利123,456,789.12元', '现金红利待核验'),
    SIMPLE.replace('三、相关日期', '共计派发现金红利99元。三、相关日期'),
    SIMPLE.replace('2026/7/17', '2027/7/17'),
    SIMPLE.replace('Ａ股', 'H股'),
    SIMPLE.replace('2025年年度\n3.', '2024年年度\n3.'),
    '扫描PDF没有文字',
])
def test_uncertain_does_not_match(text):
    result = extract(text)
    assert result['status'] == 'pending_verification'
    assert result['financial_verified'] is False


def test_future_disclosure_and_no_inferred_amount():
    assert extract(disclosed_on='2026-11-01')['event_proposals'] == []
    result = extract(SIMPLE.replace('共计派发现金红利123,456,789.12元。', ''))
    assert result['event_proposals'][0]['amount_yuan'] is None


def test_all_share_amount_does_not_acquire_a_payment():
    result = extract(SIMPLE.replace('三、相关日期', 'H股另行安排。三、相关日期'))
    current = result['event_proposals'][0]
    assert current['share_class'] == 'ALL'
    assert current['paid_on'] is None
    assert current['status'] == 'pending_verification'


@pytest.mark.parametrize('damage', ['filename', 'sha', 'header', 'size', 'symbol'])
def test_archive_safety(tmp_path, damage):
    payload = b'%PDF-1.7\ninvalid parser contents'
    filename = sha256(URL.encode()).hexdigest() + '.pdf'
    item = {'source_url': URL, 'symbol': '600123', 'disclosed_on': '2026-07-01',
            'status': 'downloaded', 'file': filename,
            'sha256': sha256(payload).hexdigest(), 'bytes': len(payload)}
    if damage == 'filename':
        item['file'] = '../outside.pdf'
    elif damage == 'sha':
        item['sha256'] = 'bad'
    elif damage == 'header':
        payload = b'HTML response'
        item['sha256'] = sha256(payload).hexdigest()
        item['bytes'] = len(payload)
    elif damage == 'size':
        item['bytes'] = 4
    else:
        item['symbol'] = '../evil'
    (tmp_path / filename).write_bytes(payload)
    result = extract_archived_documents({'items': [item]}, tmp_path, date(2026, 10, 14))
    assert result['items'][0]['status'] == 'pending_verification'
    assert result['event_proposals'] == []
    assert result['financial_verified'] is False



def test_suffix_correction_and_repurchase_unknown_shares():
    corrected = SIMPLE.replace('实施公告', '实施公告的更正公告')
    assert extract(corrected)['event_proposals'] == []
    repurchase = SIMPLE.replace('以公司总股本', '剔除回购股份，以公司总股本')
    result = extract(repurchase)
    assert result['event_proposals'][0]['eligible_shares'] is None
    assert result['status'] == 'pending_verification'
    assert any('实际参与股本' in reason for reason in result['reasons'])


def test_archive_passes_verified_bytes_and_preserves_review_state(tmp_path, monkeypatch):
    from io import BytesIO

    import radar.stocks.dividend_extract as module

    payload = b'%PDF-1.7\nverified payload'
    filename = sha256(URL.encode()).hexdigest() + '.pdf'
    item = {'source_url': URL, 'symbol': '600123', 'disclosed_on': '2026-07-01',
            'status': 'downloaded', 'file': filename,
            'sha256': sha256(payload).hexdigest(), 'bytes': len(payload)}
    path = tmp_path / filename
    path.write_bytes(payload)

    class Page:
        def extract_text(self):
            return SIMPLE

    class Reader:
        def __init__(self, stream):
            assert isinstance(stream, BytesIO)
            assert stream.getvalue() == payload
            path.write_bytes(b'%PDF-replaced after validation')
            self.pages = [Page()]

    monkeypatch.setattr(module, 'PdfReader', Reader)
    result = extract_archived_documents({'items': [item]}, tmp_path, date(2026, 10, 14))
    assert result['items'][0]['status'] == 'matched_template'
    assert result['event_proposals'][0]['amount_yuan'] == '123456789.12'
    assert result['financial_verified'] is False
    item['discovery_status'] = 'pending_review'
    result = extract_archived_documents({'items': [item]}, tmp_path, date(2026, 10, 14))
    assert result['event_proposals'] == []
    assert '尚待人工确认' in result['items'][0]['reasons'][0]


@pytest.mark.parametrize('symbol', ['000651', '600036', '601398'])
def test_template_not_company_specific(symbol):
    result = extract(SIMPLE.replace('600123', symbol), symbol=symbol)
    assert result['event_proposals'][0]['symbol'] == symbol
    assert result['event_proposals'][0]['amount_yuan'] == '123456789.12'


@pytest.mark.parametrize(('filename', 'symbol', 'amount', 'shares', 'period'), [
    ('cninfo-600900-2025-interim-implementation.txt', '600900', '5138325720.36', '24468217716', 'interim'),
    ('cninfo-600900-2024-interim-implementation.txt', '600900', '5138325720.36', '24468217716', 'interim'),
    ('cninfo-601088-2025-interim-implementation.txt', '601088', '16161217195.9', None, 'interim'),
    ('cninfo-000333-2025-interim-implementation.txt', '000333', '3448324163.5', '6896648327', 'interim'),
    ('cninfo-000333-2024-annual-implementation.txt', '000333', '24443728680', '6983922480', 'annual'),
])
def test_actual_interim_and_tip_variants(filename, symbol, amount, shares, period):
    result = extract((RESOURCES / filename).read_text(encoding='utf-8'), symbol=symbol)
    assert result['status'] == 'matched_template', result['reasons']
    row = result['event_proposals'][0]
    assert row['amount_yuan'] == amount
    assert row['eligible_shares'] == shares
    assert row['distribution_period'] == period
    assert row['financial_verified'] is False


def test_explicit_amount_relation_conflict_and_rounding():
    wrong = SIMPLE.replace('123,456,789.12元', '999元')
    result = extract(wrong)
    assert result['status'] == 'pending_verification'
    assert any('矛盾' in reason for reason in result['reasons'])
    rounded = SIMPLE.replace('1.2345678912', '1.23456789128')
    assert extract(rounded)['status'] == 'matched_template'
    beyond = SIMPLE.replace('1.2345678912', '1.2345678924')
    assert extract(beyond)['status'] == 'pending_verification'


def test_title_period_must_match_explicit_period():
    text = SIMPLE.replace('发放年度：2025年年度', '发放年度：2025年中期')
    assert extract(text)['status'] == 'pending_verification'


def test_conflicting_actual_share_bases_cannot_fall_back_to_gross_shares():
    text = SIMPLE.replace('三、相关日期', '可参与分配的股数200000000股，可参与分配的股数300000000股。三、相关日期')
    result = extract(text)
    assert result['status'] == 'pending_verification'
    assert result['event_proposals'][0]['eligible_shares'] is None
    assert any('实际参与股本存在冲突' in reason for reason in result['reasons'])

@pytest.mark.parametrize(('filename', 'year', 'period', 'paid', 'amount'), [
    ('cninfo-600036-2025-annual-implementation.txt', 2025, 'annual', '2026-07-10', '206.91'),
    ('cninfo-600036-2025-interim-implementation.txt', 2025, 'interim', '2026-01-16', '208.97'),
    ('cninfo-600036-2024-annual-implementation.txt', 2024, 'annual', '2025-07-11', '412.58'),
])
def test_real_bank_a_share_approximate_amount_never_becomes_exact(filename, year, period, paid, amount):
    # 巨潮招商银行原PDF于2026-11-02采样，裁切至相关日期；来源与SHA见项目实施记录。
    result = extract((RESOURCES / filename).read_text(encoding='utf-8'), symbol='600036')
    assert result['status'] == 'pending_verification'
    assert len(result['event_proposals']) == 1
    proposal = result['event_proposals'][0]
    assert (proposal['fiscal_year'], proposal['distribution_period'], proposal['share_class'], proposal['paid_on']) == (year, period, 'A', paid)
    assert proposal['amount_yuan'] is None
    assert proposal['approximate_amount'] == {'value': amount, 'unit': '亿元', 'currency': 'CNY'}
    assert proposal['financial_verified'] is False


def test_bank_correction_and_conflicting_approximate_values_never_pass():
    text = (RESOURCES / 'cninfo-600036-2025-interim-implementation.txt').read_text(encoding='utf-8')
    corrected = text.replace('分红派息实施公告', '分红派息实施公告更正公告')
    assert extract(corrected, symbol='600036')['event_proposals'] == []
    conflicted = text.replace('208.97', '208.97亿元，派发A股现金红利约人民币209.97')
    result = extract(conflicted, symbol='600036')
    assert result['status'] == 'pending_verification'
    assert result['event_proposals'][0]['approximate_amount'] is None

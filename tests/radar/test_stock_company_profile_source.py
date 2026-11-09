"""上市名录来源的日期与完整三周年回归。"""
import hashlib
import json
from datetime import date

import pytest

from radar.stocks.company_profile_source import load_company_profile_source
from radar.stocks.official_roster import _url
from tests.radar.test_stock_market_value_source import inputs
from tests.radar.test_stock_official_roster import body, sse

DAY = date(2026, 10, 18)


def setup_source(tmp_path, listed='20031118'):
    root = tmp_path / 'stock-company-profile'
    root.mkdir()
    args = inputs(root)
    payload = sse()
    payload['result'][0]['LIST_DATE'] = listed
    raw = body(payload)
    (root / 'roster.json').write_bytes(raw)
    manifest = {'version': 'stock-company-profile-v1', 'as_of': DAY.isoformat(),
                'scope': {'path': 'scope.json', 'sha256': args['scope_sha256'], 'bytes': args['scope_path'].stat().st_size},
                'rosters': [{'path': 'roster.json', 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                             'source': 'sse_main', 'source_url': _url('sse_main'), 'fetched_at': '2026-10-18T16:00:00+08:00'}]}
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return root, manifest


@pytest.mark.parametrize(('listed','outcome'), [('20231018','passed'),('20231019','failed'),('20000229','passed')])
def test_age_boundary(tmp_path, listed, outcome):
    setup_source(tmp_path, listed)
    result = load_company_profile_source(tmp_path, '600900', DAY)
    assert result['status'] == 'current'
    assert result['checks'][0]['outcome'] == outcome
    assert result['name_match'] is True
    if listed == '20000229':
        assert result['checks'][0]['values']['third_anniversary'] == '2003-02-28'
    assert str(tmp_path) not in json.dumps(result)


def test_history_and_corruption(tmp_path):
    root, _ = setup_source(tmp_path)
    assert load_company_profile_source(tmp_path, '600900', date(2026,10,19))['status'] == 'historical'
    (root / 'roster.json').write_text('{}')
    assert load_company_profile_source(tmp_path, '600900', DAY)['status'] == 'unavailable'


@pytest.mark.parametrize('change', ['duplicate','missing','escape','future','wrong_url'])
def test_invalid_sources_fail_closed(tmp_path, change):
    root, manifest = setup_source(tmp_path)
    if change == 'duplicate': manifest['rosters'].append(manifest['rosters'][0].copy())
    if change == 'missing': manifest['rosters'] = []
    if change == 'escape': manifest['rosters'][0]['path'] = '../outside'
    if change == 'future': manifest['rosters'][0]['fetched_at'] = '2099-01-01T00:00:00+08:00'
    if change == 'wrong_url': manifest['rosters'][0]['source_url'] = 'https://example.com'
    (root / 'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    assert load_company_profile_source(tmp_path,'600900',DAY)['status'] == 'unavailable'


def test_missing_read_creates_nothing(tmp_path):
    assert load_company_profile_source(tmp_path,'600900',DAY)['status'] == 'unavailable'
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize('damage', ['name','missing_code','duplicate_code','bad_date'])
def test_row_identity_and_name_boundaries(tmp_path, damage):
    root, manifest = setup_source(tmp_path)
    value = json.loads((root / 'roster.json').read_bytes())
    if damage == 'name': value['result'][0]['SEC_NAME_CN'] = '不同名称'
    if damage == 'missing_code': value['result'][0]['A_STOCK_CODE'] = '600901'
    if damage == 'duplicate_code': value['result'].append(value['result'][0].copy())
    if damage == 'bad_date': value['result'][0]['LIST_DATE'] = 'bad'
    value['pageHelp']['total'] = len(value['result'])
    raw = body(value)
    (root / 'roster.json').write_bytes(raw)
    manifest['rosters'][0].update(sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    result = load_company_profile_source(tmp_path,'600900',DAY)
    if damage == 'name':
        assert result['status'] == 'current' and result['name_match'] is False
        assert result['checks'][0]['outcome'] == 'passed'
        assert result['security_market']['status'] == 'pending_verification'
    else: assert result['status'] == 'unavailable'


def test_replacement_during_parse_fails_closed(tmp_path, monkeypatch):
    import radar.stocks.company_profile_source as module
    root, _ = setup_source(tmp_path)
    parser = module._parse_source
    def changing(raw, source):
        parsed = parser(raw, source)
        (root / 'manifest.json').write_text('{}')
        return parsed
    monkeypatch.setattr(module, '_parse_source', changing)
    assert module.load_company_profile_source(tmp_path,'600900',DAY)['status'] == 'unavailable'


def test_profile_exposes_market_identity_and_issuer_classification(tmp_path):
    setup_source(tmp_path)
    result = load_company_profile_source(tmp_path, '600900', DAY)
    identity = result['security_market']
    assert identity['status'] == 'verified'
    assert identity['exchange'] == 'SSE' and identity['security_type'] == 'ordinary_a'
    assert identity['issuer_type'] == 'ordinary' and identity['issuer_type_status'] == 'verified'
    assert identity['symbol'] == '600900'
    assert identity['source_sha256'] == result['source']['rosters'][0]['sha256']


@pytest.mark.parametrize('fetched', ['2026-10-17T08:00:00+08:00', '2026-10-19T08:00:00+08:00'])
def test_profile_fetched_outside_cutoff_cannot_be_current(tmp_path, fetched):
    root, manifest = setup_source(tmp_path)
    manifest['rosters'][0]['fetched_at'] = fetched
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    result = load_company_profile_source(tmp_path, '600900', DAY)
    assert result['status'] == 'historical'
    assert result['security_market']['status'] == 'historical'

def test_market_name_normalizes_spaces_fullwidth_and_xd_prefix():
    from radar.stocks.company_profile_source import _names_match

    assert _names_match('万  科Ａ', '万科A', '万科企业股份有限公司')
    assert _names_match('新 和 成', '新和成', '浙江新和成股份有限公司')
    assert _names_match('XD方大炭素', '方大炭素', '方大炭素新材料科技股份有限公司')
    assert _names_match('XD中国人保', '中国人保', '中国人民保险集团股份有限公司')
    # 截断简称不再按前缀猜身份，避免把不同主体当成同一家。
    assert not _names_match('XD方大炭', '方大炭素', '方大炭素新材料科技股份有限公司')
    assert not _names_match('万科A', '保利发展', '保利发展控股集团股份有限公司')
    assert not _names_match('', '', '')

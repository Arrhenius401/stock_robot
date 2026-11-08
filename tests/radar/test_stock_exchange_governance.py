"""交易所监管列表仅作为采集时点研究线索。"""
import hashlib
import json
from datetime import datetime, timedelta

import pytest

from radar.stocks.admission import _BJ


def today():
    return datetime.now(_BJ).date()


def sse_page(rows, total=None, page=1, pages=1):
    payload = {'pageHelp': {'data': rows, 'total': len(rows) if total is None else total,
                            'pageCount': pages, 'pageNo': page}}
    return ('probe(' + json.dumps(payload, ensure_ascii=False) + ')').encode()


def szse_page(rows, total=None, page=1, pages=1):
    payload = [{'metadata': {'recordcount': len(rows) if total is None else total,
                             'pagecount': pages, 'pageno': page}, 'data': rows}]
    return json.dumps(payload, ensure_ascii=False).encode()


def sample():
    sse = [{'stockcode': '600000', 'docId': '1_28_10010', 'docTitle': '关于A公司的监管工作函',
            'createTime': '2026-11-02 19:07:19', 'extWTFL': '监管工作函', 'extTeacher': '上市公司',
            'docURL': 'www.sse.com.cn/disclosure/a.doc'},
           {'stockcode': '600001', 'docId': '2_28_10010', 'docTitle': '关于对B公司予以监管关注的决定',
            'createTime': '2026-10-30 21:15:08', 'extWTFL': '监管关注', 'extTeacher': '上市公司,董秘',
            'docURL': 'www.sse.com.cn/disclosure/b.doc'}]
    measure = [{'gkxx_gsdm': '000001', 'gkxx_gsjc': '平安银行', 'gkxx_gdrq': '2026-11-06',
                'gkxx_jgcs': '监管工作函', 'gkxx_sjdx': '上市公司<br/>董监高',
                'hjnr': "<a href='javascript:void(0)' encode-open='/UpFiles/a.pdf'>详细</a>"}]
    discipline = [{'xx_gsdm': '000002', 'jc_gsjc': '万科A', 'xx_fwrq': '2026-11-06',
                   'xx_cflb': '通报批评', 'xx_bt': '纪律处分决定', 'ck': "<a encode-open='/UpFiles/b.pdf'>查看</a>"}]
    return sse, measure, discipline


def fake_fetch(sse_rows, measure_rows, discipline_rows, calls=None, sse_pages=None, measure_pages=None):
    def fetch(url, params, referer):
        if calls is not None:
            calls.append((url, dict(params)))
        if 'sse.com.cn' in url:
            if sse_pages is not None:
                page = int(params['pageHelp.pageNo'])
                return 200, sse_page(sse_pages[page], total=sum(len(v) for v in sse_pages.values()),
                                     page=page, pages=len(sse_pages))
            return 200, sse_page(sse_rows, total=len(sse_rows), pages=1)
        if params.get('CATALOGID') == '1800_jgxxgk_snapshot':
            if measure_pages is not None:
                page = int(params['PAGENO'])
                return 200, szse_page(measure_pages[page], total=sum(len(v) for v in measure_pages.values()),
                                      page=page, pages=len(measure_pages))
            return 200, szse_page(measure_rows, total=len(measure_rows), pages=1)
        return 200, szse_page(discipline_rows, total=len(discipline_rows), pages=1)
    return fetch


def save_index(root, index):
    raw = json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    (root / 'index.json').write_bytes(raw)
    manifest = json.loads((root / 'manifest.json').read_bytes())
    manifest['index'] = {'path': 'index.json', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    manifest['records'] = index['records']
    manifest['unmatched'] = index['unmatched']
    manifest['invalid'] = index['invalid']
    (root / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding='utf-8')


def legacy_snapshot(root):
    index = json.loads((root / 'index.json').read_bytes())
    index['version'] = 'exchange-governance-v1'
    for rows in index['symbols'].values():
        for row in rows:
            if row.get('measure_type') == '监管关注':
                row['measure_category'] = 'work_letter'
    save_index(root, index)
    manifest = json.loads((root / 'manifest.json').read_bytes())
    manifest['version'] = 'exchange-governance-v1'
    (root / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding='utf-8')


def test_collect_and_load_clues(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
    )

    calls = []
    day = today()
    result = collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample(), calls=calls), sleep=lambda _: None)
    assert result['status'] == 'complete' and result['records'] == 4
    assert result['sources']['sse'] == {'declared_total': 2, 'received': 2, 'dropped': 0}
    assert len(calls) == 3
    sse = load_exchange_governance_source(tmp_path, '600000', day)
    assert sse['status'] == 'current' and sse['observation'] == 'observed'
    assert sse['records'][0]['measure_type'] == '监管工作函'
    assert sse['records'][0]['measure_category'] == 'work_letter'
    assert sse['records'][0]['url'] == 'https://www.sse.com.cn/disclosure/a.doc'
    assert len(sse['records'][0]['page_sha256']) == 64
    assert sse['manifest_sha256'] and sse['index_sha256']
    assert sse['coverage']['sse']['raw_received'] == 2
    measure = load_exchange_governance_source(tmp_path, '000001', day)
    assert measure['records'][0]['source'] == 'szse_measure'
    assert measure['records'][0]['object'] == '上市公司/董监高'
    assert measure['records'][0]['url'] == 'https://www.szse.cn/UpFiles/a.pdf'
    assert measure['records'][0]['measure_category'] == 'work_letter'
    discipline = load_exchange_governance_source(tmp_path, '000002', day)
    assert discipline['records'][0]['measure_type'] == '通报批评'
    assert discipline['records'][0]['measure_category'] == 'discipline'
    assert load_exchange_governance_source(tmp_path, '600002', day)['observation'] == 'not_observed'


def test_historical_and_unavailable_cutoffs(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    day = today()
    assert load_exchange_governance_source(tmp_path, '600000', day + timedelta(days=1))['status'] == 'historical'
    assert load_exchange_governance_source(tmp_path, '600000', day - timedelta(days=30))['status'] == 'unavailable'
    assert load_exchange_governance_source(tmp_path, '600000', day)['reviewed'] is False
    assert load_exchange_governance_source(tmp_path, '600000', day)['formal_screening_enabled'] is False


def test_duplicate_document_rejected(tmp_path):
    from radar.stocks.exchange_governance import collect_exchange_governance

    sse, measure, discipline = sample()
    sse = [dict(sse[0]), dict(sse[0])]
    result = collect_exchange_governance(tmp_path, fetch=fake_fetch(sse, measure, discipline), sleep=lambda _: None)
    assert result['status'] == 'failed'


def test_szse_fallback_duplicate_rejected(tmp_path):
    from radar.stocks.exchange_governance import collect_exchange_governance

    sse, measure, discipline = sample()
    duplicate = {key: value for key, value in measure[0].items() if key not in ('hjnr',)}
    result = collect_exchange_governance(tmp_path, fetch=fake_fetch(sse, [duplicate, dict(duplicate)], discipline),
                                         sleep=lambda _: None)
    assert result['status'] == 'failed'


def test_total_mismatch_rejected(tmp_path):
    from radar.stocks.exchange_governance import collect_exchange_governance

    sse, measure, discipline = sample()

    def fetch(url, params, referer):
        if 'sse.com.cn' in url:
            return 200, sse_page(sse, total=3, pages=1)
        return 200, szse_page(measure if params.get('CATALOGID') == '1800_jgxxgk_snapshot' else discipline)

    assert collect_exchange_governance(tmp_path, fetch=fetch, sleep=lambda _: None)['status'] == 'failed'


def test_multipage_and_short_last_page(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
    )

    sse, measure, discipline = sample()
    pages = {1: [sse[0]], 2: [sse[1]]}
    calls = []
    result = collect_exchange_governance(
        tmp_path, fetch=fake_fetch(sse, measure, discipline, calls=calls, sse_pages=pages), sleep=lambda _: None)
    assert result['status'] == 'complete'
    assert any(params.get('pageHelp.beginPage') == '2' and params.get('pageHelp.endPage') == '2'
               for url, params in calls if 'sse.com.cn' in url)
    day = today()
    assert load_exchange_governance_source(tmp_path, '600000', day)['coverage']['sse']['pages'] == 2
    assert load_exchange_governance_source(tmp_path, '600001', day)['observation'] == 'observed'


@pytest.mark.parametrize('change', ['total', 'pages'])
def test_page_metadata_change_rejected(tmp_path, change):
    from radar.stocks.exchange_governance import collect_exchange_governance

    sse, measure, discipline = sample()

    def fetch(url, params, referer):
        if 'sse.com.cn' in url:
            page = int(params['pageHelp.pageNo'])
            return 200, sse_page(sse[:1] if page == 1 else sse[1:],
                                 total=2 if change == 'total' or page == 1 else 3,
                                 pages=2 if change == 'pages' or page == 1 else 1, page=page)
        return 200, szse_page(measure if params.get('CATALOGID') == '1800_jgxxgk_snapshot' else discipline)

    assert collect_exchange_governance(tmp_path, fetch=fetch, sleep=lambda _: None)['status'] == 'failed'


def test_cross_page_duplicate_rejected(tmp_path):
    from radar.stocks.exchange_governance import collect_exchange_governance

    sse, measure, discipline = sample()
    result = collect_exchange_governance(tmp_path,
                                         fetch=fake_fetch(sse, measure, discipline, sse_pages={1: [sse[0]], 2: [sse[0]]}),
                                         sleep=lambda _: None)
    assert result['status'] == 'failed'


def test_invalid_codes_are_not_bound_to_companies(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
    )

    sse, measure, discipline = sample()
    bad = [{'stockcode': code, 'docId': 'bad-' + str(index), 'docTitle': '无效证券样本',
            'createTime': '2026-11-02 19:07:19', 'extWTFL': '监管工作函', 'extTeacher': '上市公司'}
           for index, code in enumerate(('900901', '510300', '６０００００'))]
    result = collect_exchange_governance(tmp_path, fetch=fake_fetch(bad + sse, measure, discipline),
                                         sleep=lambda _: None)
    assert result['status'] == 'complete' and result['unmatched'] == 3
    assert load_exchange_governance_source(tmp_path, '600000', today())['records'][0]['listed_on'] == '2026-11-02'


def test_dropped_rows_are_reported(tmp_path):
    from radar.stocks.exchange_governance import collect_exchange_governance

    sse, measure, discipline = sample()
    empty = {'docId': 'empty-1'}
    result = collect_exchange_governance(tmp_path, fetch=fake_fetch([None, empty, sse[0]], measure, discipline),
                                         sleep=lambda _: None)
    assert result['status'] == 'complete'
    assert result['sources']['sse']['declared_total'] == 3
    assert result['sources']['sse']['received'] == 1
    assert result['sources']['sse']['dropped'] == 2
    assert result['invalid'] == 2


def test_measure_categories_are_conservative(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
    )

    sse, measure, discipline = sample()
    sse = [*sse, {'stockcode': '600002', 'docId': '3_28_10010', 'docTitle': '纪律处分决定',
                  'createTime': '2026-11-02 19:07:19', 'extWTFL': '公开谴责', 'extTeacher': '上市公司'}]
    collect_exchange_governance(tmp_path, fetch=fake_fetch(sse, measure, discipline), sleep=lambda _: None)
    day = today()
    assert load_exchange_governance_source(tmp_path, '600000', day)['records'][0]['measure_category'] == 'work_letter'
    assert load_exchange_governance_source(tmp_path, '600001', day)['records'][0]['measure_category'] == 'regulatory_measure'
    assert load_exchange_governance_source(tmp_path, '600002', day)['records'][0]['measure_category'] == 'discipline'


def test_tampered_index_unavailable(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    (tmp_path / 'stock-exchange-governance' / 'index.json').write_bytes(b'{}')
    assert load_exchange_governance_source(tmp_path, '600000', today())['status'] == 'unavailable'


@pytest.mark.parametrize('field,value', [('records', 99), ('fetched_at', '2020-01-01T00:00:00+08:00')])
def test_tampered_manifest_unavailable(tmp_path, field, value):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    path = tmp_path / 'stock-exchange-governance' / 'manifest.json'
    manifest = json.loads(path.read_bytes())
    manifest[field] = value
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    assert load_exchange_governance_source(tmp_path, '600000', today())['status'] == 'unavailable'


def test_tampered_source_url_unavailable(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    path = tmp_path / 'stock-exchange-governance' / 'manifest.json'
    manifest = json.loads(path.read_bytes())
    manifest['sources']['sse']['url'] = 'https://example.org/'
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    assert load_exchange_governance_source(tmp_path, '600000', today())['status'] == 'unavailable'


def test_failure_disables_old_snapshot(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    failed = collect_exchange_governance(tmp_path, fetch=lambda url, params, referer: (500, b''), sleep=lambda _: None)
    assert failed['status'] == 'failed'
    assert load_exchange_governance_source(tmp_path, '600000', today())['status'] == 'unavailable'


def test_verify_rejects_page_tamper(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        verify_exchange_governance,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    root = tmp_path / 'stock-exchange-governance'
    (root / 'sse-001.json').write_bytes(b'{}')
    assert verify_exchange_governance(tmp_path)['status'] == 'unavailable'


def test_load_rejects_any_tampered_page(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    root = tmp_path / 'stock-exchange-governance'
    (root / 'sse-001.json').write_bytes(b'{}')
    assert load_exchange_governance_source(tmp_path, '600000', today())['status'] == 'unavailable'
    assert load_exchange_governance_source(tmp_path, '000001', today())['status'] == 'unavailable'


def test_missing_page_cannot_return_not_observed(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    (tmp_path / 'stock-exchange-governance' / 'sse-001.json').unlink()
    result = load_exchange_governance_source(tmp_path, '000001', today())
    assert result['status'] == 'unavailable' and result['observation'] == 'unknown'


def test_index_wrongly_bound_to_company_is_rejected(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
        verify_exchange_governance,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    root = tmp_path / 'stock-exchange-governance'
    index = json.loads((root / 'index.json').read_bytes())
    index['symbols'].setdefault('000001', []).append(index['symbols']['600000'].pop())
    save_index(root, index)
    assert verify_exchange_governance(tmp_path)['status'] == 'unavailable'
    assert load_exchange_governance_source(tmp_path, '000001', today())['status'] == 'unavailable'


@pytest.mark.parametrize('change', ['content', 'delete'])
def test_index_content_or_record_change_is_rejected(tmp_path, change):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        verify_exchange_governance,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    root = tmp_path / 'stock-exchange-governance'
    index = json.loads((root / 'index.json').read_bytes())
    if change == 'content':
        index['symbols']['600000'][0]['title'] = '篡改后的标题'
    else:
        index['symbols']['600000'].pop()
        index['records'] -= 1
    save_index(root, index)
    assert verify_exchange_governance(tmp_path)['status'] == 'unavailable'


def test_category_mapping_is_explicit_and_unknown_preserved(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
    )

    sse, measure, discipline = sample()
    sse = [*sse,
           {'stockcode': '600003', 'docId': '3_28_10010', 'docTitle': '关于C公司的监管函',
            'createTime': '2026-11-02 19:07:19', 'extWTFL': '监管函', 'extTeacher': '上市公司'},
           {'stockcode': '600004', 'docId': '4_28_10010', 'docTitle': '关于D公司的书面警示',
            'createTime': '2026-11-02 19:07:19', 'extWTFL': '书面警示', 'extTeacher': '上市公司'}]
    collect_exchange_governance(tmp_path, fetch=fake_fetch(sse, measure, discipline), sleep=lambda _: None)
    day = today()
    assert load_exchange_governance_source(tmp_path, '600000', day)['records'][0]['measure_category'] == 'work_letter'
    assert load_exchange_governance_source(tmp_path, '600001', day)['records'][0]['measure_category'] == 'regulatory_measure'
    assert load_exchange_governance_source(tmp_path, '600003', day)['records'][0]['measure_category'] == 'unknown'
    assert load_exchange_governance_source(tmp_path, '600004', day)['records'][0]['measure_category'] == 'regulatory_measure'


def test_legacy_snapshot_rebuild_preserves_capture_time_and_fixes_category(tmp_path):
    from radar.stocks.exchange_governance import (
        VERSION,
        collect_exchange_governance,
        load_exchange_governance_source,
        rebuild_exchange_governance_index,
        verify_exchange_governance,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    root = tmp_path / 'stock-exchange-governance'
    fetched_at = json.loads((root / 'manifest.json').read_bytes())['fetched_at']
    legacy_snapshot(root)
    assert load_exchange_governance_source(tmp_path, '600001', today())['status'] == 'unavailable'
    result = rebuild_exchange_governance_index(tmp_path)
    assert result['status'] == 'rebuilt' and result['from_version'] == 'exchange-governance-v1'
    manifest = json.loads((root / 'manifest.json').read_bytes())
    assert manifest['version'] == VERSION and manifest['fetched_at'] == fetched_at
    assert manifest['as_of'] == result['as_of']
    assert verify_exchange_governance(tmp_path)['status'] == 'current'
    clue = load_exchange_governance_source(tmp_path, '600001', today())
    assert clue['records'][0]['measure_category'] == 'regulatory_measure'
    assert clue['fetched_at'] == fetched_at


def test_legacy_rebuild_failure_keeps_snapshot_unavailable(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
        rebuild_exchange_governance_index,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    root = tmp_path / 'stock-exchange-governance'
    legacy_snapshot(root)
    (root / 'sse-001.json').unlink()
    assert rebuild_exchange_governance_index(tmp_path)['status'] == 'failed'
    assert load_exchange_governance_source(tmp_path, '600001', today())['status'] == 'unavailable'


def test_snapshot_identity_is_light_and_hash_bound(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        governance_snapshot_identity,
    )

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    identity = governance_snapshot_identity(tmp_path)
    assert identity is not None and len(identity['manifest_sha256']) == 64
    (tmp_path / 'stock-exchange-governance' / 'index.json').write_bytes(b'{}')
    assert governance_snapshot_identity(tmp_path) is None


def test_omitted_page_with_synchronized_registration_rejected(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        load_exchange_governance_source,
        verify_exchange_governance,
    )

    sse, measure, discipline = sample()
    collect_exchange_governance(
        tmp_path, fetch=fake_fetch(sse, measure, discipline, sse_pages={1: [sse[0]], 2: [sse[1]]}),
        sleep=lambda _: None)
    root = tmp_path / 'stock-exchange-governance'
    manifest = json.loads((root / 'manifest.json').read_bytes())
    source = manifest['sources']['sse']
    source['pages'] = source['pages'][:1]
    source['page_count'] = 1
    source['declared_total'] = 1
    source['raw_received'] = 1
    source['parsed'] = 1
    source['received'] = 1
    (root / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    index = json.loads((root / 'index.json').read_bytes())
    index['symbols'].pop('600001', None)
    index['records'] -= 1
    save_index(root, index)
    # 登记、索引均已同步为 1 页/3 条，但原文第 1 页仍声明 2 页/2 条。
    assert verify_exchange_governance(tmp_path)['status'] == 'unavailable'
    assert load_exchange_governance_source(tmp_path, '600001', today())['status'] == 'unavailable'


def test_raw_page_number_must_match_registration(tmp_path):
    from radar.stocks.exchange_governance import (
        collect_exchange_governance,
        verify_exchange_governance,
    )

    sse, measure, discipline = sample()
    collect_exchange_governance(
        tmp_path, fetch=fake_fetch(sse, measure, discipline, sse_pages={1: [sse[0]], 2: [sse[1]]}),
        sleep=lambda _: None)
    root = tmp_path / 'stock-exchange-governance'
    raw = sse_page([sse[1]], total=2, pages=2, page=1)
    (root / 'sse-002.json').write_bytes(raw)
    manifest = json.loads((root / 'manifest.json').read_bytes())
    ref = manifest['sources']['sse']['pages'][1]
    ref['sha256'] = hashlib.sha256(raw).hexdigest()
    ref['bytes'] = len(raw)
    (root / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    assert verify_exchange_governance(tmp_path)['status'] == 'unavailable'


def test_command_uses_explicit_state(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import stock_robot.stock_governance as module

    called = {}

    def fake(state_dir):
        called['state'] = state_dir
        return {'status': 'complete', 'records': 4}

    monkeypatch.setattr(module, 'collect_exchange_governance', fake)
    result = CliRunner().invoke(module.exchange_governance_update, ['--state-dir', str(tmp_path)])
    assert result.exit_code == 0 and called['state'] == tmp_path
    assert '未生成审核或正式成员' in result.output


def test_page_files_and_manifest_are_hash_bound(tmp_path):
    from radar.stocks.exchange_governance import collect_exchange_governance

    collect_exchange_governance(tmp_path, fetch=fake_fetch(*sample()), sleep=lambda _: None)
    root = tmp_path / 'stock-exchange-governance'
    manifest = json.loads((root / 'manifest.json').read_bytes())
    assert manifest['status'] == 'complete' and manifest['records'] == 4
    for source in manifest['sources'].values():
        for page in source['pages']:
            assert hashlib.sha256((root / page['path']).read_bytes()).hexdigest() == page['sha256']


def test_rebuild_command_uses_explicit_state(tmp_path, monkeypatch):
    from click.testing import CliRunner

    import stock_robot.stock_governance as module

    called = {}

    def fake(state_dir):
        called['state'] = state_dir
        return {'status': 'rebuilt', 'from_version': 'exchange-governance-v1'}

    monkeypatch.setattr(module, 'rebuild_exchange_governance_index', fake)
    result = CliRunner().invoke(module.exchange_governance_rebuild, ['--state-dir', str(tmp_path)])
    assert result.exit_code == 0 and called['state'] == tmp_path
    assert '未抓取新数据' in result.output

"""交易所上市公司监管列表只读研究线索；不产生审核事实或资格结论。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
from filelock import FileLock, Timeout

from radar.provider_request import ProviderRequestError, bounded_provider_call
from radar.stocks.admission import _BJ
from radar.stocks.market_history import _atomic
from radar.stocks.market_value_registry import _bytes, _path, _reference
from radar.stocks.research_store import _symbol
from radar.stocks.structured_collect import _json

logger = logging.getLogger(__name__)
VERSION = 'exchange-governance-v3'
LEGACY_VERSIONS = {'exchange-governance-v1', 'exchange-governance-v2'}
SSE_URL = 'https://query.sse.com.cn/commonSoaQuery.do'
SSE_BASE = 'https://www.sse.com.cn'
SSE_REFERER = 'https://www.sse.com.cn/disclosure/credential/supervision/measures/'
SSE_PARAMS = {'jsonCallBack': 'probe', 'isPagination': 'true', 'sqlId': 'BS_KCB_GGLL_NEW', 'siteId': '28',
              'channelId': '10007,10008,10009,10010', 'order': 'createTime|desc,stockcode|asc', 'type': '',
              'pageHelp.pageSize': '25', 'pageHelp.beginPage': '1', 'pageHelp.cacheSize': '1',
              'pageHelp.endPage': '1'}
SSE_PAGE_SIZE = 25
SZSE_URL = 'https://www.szse.cn/api/report/ShowReport/data'
SZSE_BASE = 'https://www.szse.cn'
SZSE_REFERER = 'https://www.szse.cn/disclosure/supervision/measure/measure/index.html'
SZSE_SOURCES = {
    'szse_measure': {'catalog': '1800_jgxxgk_snapshot', 'tabkey': 'tab1', 'label': '深交所监管措施'},
    'szse_discipline': {'catalog': '1800_jgxxgk_cf_snapshot', 'tabkey': 'tab2', 'label': '深交所纪律处分'},
}
SZSE_PAGE_SIZE = 20
SOURCE_KEYS = {'sse', 'szse_measure', 'szse_discipline'}
_MAX_BODY = 4 * 1024 * 1024
_ALLOWED_HOSTS = {'www.sse.com.cn', 'www.szse.cn', 'query.sse.com.cn'}
_SSE_CODE = re.compile(r'(?:60[0135]|688)[0-9]{3}')
_SZSE_CODE = re.compile(r'(?:000|001|002|003|30[012])[0-9]{3}')
_DISCIPLINE_TYPES = {'通报批评', '公开谴责', '公开认定'}
_MEASURE_TYPES = {'监管关注', '书面警示', '监管警示', '约见谈话', '要求全体董事限期参加培训'}
_WORK_LETTER_TYPES = {'监管工作函'}
_PAGE_CACHE: dict[tuple[str, str], tuple[int, tuple[dict[str, Any], ...]]] = {}
_PAGE_CACHE_LIMIT = 320


def _download(url: str, params: dict[str, str], referer: str) -> tuple[int, bytes]:
    response = requests.get(url, params=params, timeout=20, stream=True, allow_redirects=False,
                            headers={'User-Agent': 'Mozilla/5.0', 'Referer': referer})
    try:
        if response.status_code != 200:
            return response.status_code, b''
        body = bytearray()
        for chunk in response.iter_content(65536):
            body.extend(chunk)
            if len(body) > _MAX_BODY:
                raise ValueError('监管列表原文超出预算')
        return response.status_code, bytes(body)
    finally:
        response.close()


def _fetch(url: str, params: dict[str, str], referer: str) -> tuple[int, bytes]:
    return bounded_provider_call(__name__, '_download', args=(url, params, referer), timeout=25)


def _sse_params(page: int) -> dict[str, str]:
    return {**SSE_PARAMS, 'pageHelp.pageNo': str(page), 'pageHelp.beginPage': str(page),
            'pageHelp.endPage': str(page)}


def _szse_params(catalog: str, tabkey: str, page: int) -> dict[str, str]:
    return {'SHOWTYPE': 'JSON', 'CATALOGID': catalog, 'TABKEY': tabkey, 'PAGENO': str(page),
            'pagesize': str(SZSE_PAGE_SIZE), 'random': '0.1'}


def _root(state_dir: Path) -> Path:
    root = Path(state_dir).absolute() / 'stock-exchange-governance'
    for path in (root, *root.parents):
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ValueError('监管列表目录不能经过链接')
    return root


def _jsonp(raw: bytes) -> dict[str, Any]:
    text = raw.decode('utf-8-sig')
    match = re.fullmatch(r'\s*[A-Za-z_$][\w$]*\((.*)\)\s*;?\s*', text, re.DOTALL)
    if match is None:
        raise ValueError('上交所监管列表不是预期JSONP格式')
    payload = _json(match.group(1).encode())
    if not isinstance(payload, dict):
        raise TypeError('上交所监管列表结构无效')
    return payload


def _page_ref(root: Path, name: str, raw: bytes) -> dict[str, Any]:
    digest = hashlib.sha256(raw).hexdigest()
    _atomic(root / name, raw)
    return {'path': name, 'bytes': len(raw), 'sha256': digest}


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _link(value: Any, base: str) -> str | None:
    text = _text(value)
    if text is None:
        return None
    if 'encode-open=' in text:
        match = re.search(r"encode-open=['\"]([^'\"]+)['\"]", text)
        text = match.group(1) if match else text
    elif 'href=' in text:
        match = re.search(r"href=['\"]([^'\"]+)['\"]", text)
        text = match.group(1) if match else text
    if text.startswith('http://'):
        text = 'https://' + text[7:]
    elif text.startswith('www.'):
        text = 'https://' + text
    elif text.startswith('/'):
        text = base + text
    if not text.startswith('https://'):
        return None
    host = urlsplit(text).hostname
    if not host or not any(host == suffix or host.endswith('.' + suffix) for suffix in ('sse.com.cn', 'szse.cn')):
        return None
    return text


def _code(value: Any, source: str) -> str | None:
    text = _text(value)
    if text is None or re.fullmatch(r'[0-9]{6}', text) is None:
        return None
    pattern = _SSE_CODE if source == 'sse' else _SZSE_CODE
    return text if pattern.fullmatch(text) else None


def _day_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.match(r'(\d{4}-\d{2}-\d{2})', value.strip())
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return None


def _category(measure_type: str | None, source: str) -> str:
    if source == 'szse_discipline' or measure_type in _DISCIPLINE_TYPES:
        return 'discipline'
    if measure_type in _MEASURE_TYPES:
        return 'regulatory_measure'
    if measure_type in _WORK_LETTER_TYPES:
        return 'work_letter'
    return 'unknown'


def _normalize(source: str, row: dict[str, Any], page_sha: str) -> dict[str, Any] | None:
    base = SZSE_BASE if source.startswith('szse') else SSE_BASE
    if source == 'sse':
        document_id = _text(row.get('docId'))
        code = _code(row.get('stockcode'), source) or _code(row.get('extSECURITY_CODE'), source)
        record = {'source': source, 'document_id': document_id, 'code': code, 'name': _text(row.get('extGSJC')),
                  'listed_on': _day_text(row.get('createTime')) or _day_text(row.get('cmsOpDate')),
                  'measure_type': _text(row.get('extWTFL')) or _text(row.get('extTYPE')),
                  'object': _text(row.get('extTeacher')), 'title': _text(row.get('docTitle')),
                  'url': _link(row.get('docURL'), base), 'page_sha256': page_sha}
    elif source == 'szse_measure':
        record = {'source': source, 'document_id': None, 'code': _code(row.get('gkxx_gsdm'), source),
                  'name': _text(row.get('gkxx_gsjc')), 'listed_on': _day_text(row.get('gkxx_gdrq')),
                  'measure_type': _text(row.get('gkxx_jgcs')),
                  'object': (_text(row.get('gkxx_sjdx')) or '').replace('<br/>', '/') or None,
                  'title': None, 'url': _link(row.get('hjnr'), base), 'page_sha256': page_sha}
    elif source == 'szse_discipline':
        record = {'source': source, 'document_id': None, 'code': _code(row.get('xx_gsdm'), source),
                  'name': _text(row.get('jc_gsjc')), 'listed_on': _day_text(row.get('xx_fwrq')),
                  'measure_type': _text(row.get('xx_cflb')), 'object': None,
                  'title': _text(row.get('xx_bt')), 'url': _link(row.get('ck'), base), 'page_sha256': page_sha}
    else:
        raise ValueError('监管列表来源无效')
    if record['code'] is None and not record['title']:
        return None
    record['measure_category'] = _category(record['measure_type'], source)
    if record['document_id'] is None:
        raw = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str).encode()
        record['document_id'] = hashlib.sha256(raw).hexdigest()[:24]
    return record


def _collect_sse(root: Path, fetch: Callable[[str, dict[str, str], str], tuple[int, bytes]],
                 sleep: Callable[[float], Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    refs, rows, expected = [], [], None
    page, page_count, raw_count = 1, None, 0
    while True:
        if page > 1:
            sleep(0.3)
        status, raw = fetch(SSE_URL, _sse_params(page), SSE_REFERER)
        if status != 200:
            raise ValueError(f'上交所监管列表HTTP{status}，停止采集')
        if not isinstance(raw, bytes) or not 0 < len(raw) <= _MAX_BODY:
            raise ValueError('上交所监管列表原文无效')
        payload = _jsonp(raw)
        help_ = payload.get('pageHelp')
        if not isinstance(help_, dict):
            raise TypeError('上交所监管列表分页结构无效')
        data = help_.get('data')
        total, declared_pages = help_.get('total'), help_.get('pageCount')
        if (type(total) is not int or not 0 < total <= 20000 or type(declared_pages) is not int
                or not 1 <= declared_pages <= 1000 or not isinstance(data, list)):
            raise ValueError('上交所监管列表总数或分页无效')
        if expected is None:
            expected = (total, declared_pages)
        elif expected != (total, declared_pages):
            raise ValueError('上交所监管列表总数或页数变化')
        ref = _page_ref(root, f'sse-{page:03d}.json', raw)
        refs.append({**ref, 'page': page, 'count': len(data)})
        raw_count += len(data)
        rows.extend(normalized for row in data if isinstance(row, dict)
                    if (normalized := _normalize('sse', row, ref['sha256'])) is not None)
        page_count = declared_pages
        if page >= page_count:
            break
        page += 1
    if expected is None or raw_count != expected[0] or len(refs) != page_count:
        raise ValueError('上交所监管列表分页不完整')
    dates = sorted(row['listed_on'] for row in rows if row.get('listed_on'))
    source = {'label': '上交所上市公司监管列表', 'url': SSE_URL, 'params': SSE_PARAMS,
              'declared_total': expected[0], 'page_count': page_count, 'raw_received': raw_count, 'parsed': len(rows),
              'dropped': raw_count - len(rows), 'received': len(rows), 'pages': refs,
              'date_range': [dates[0], dates[-1]] if dates else None}
    return source, rows


def _collect_szse(root: Path, key: str, spec: dict[str, str],
                  fetch: Callable[[str, dict[str, str], str], tuple[int, bytes]],
                  sleep: Callable[[float], Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    refs, rows, expected = [], [], None
    page, page_count, raw_count = 1, None, 0
    while True:
        if page > 1:
            sleep(0.3)
        status, raw = fetch(SZSE_URL, _szse_params(spec['catalog'], spec['tabkey'], page), SZSE_REFERER)
        if status != 200:
            raise ValueError(f'{spec["label"]}HTTP{status}，停止采集')
        if not isinstance(raw, bytes) or not 0 < len(raw) <= _MAX_BODY:
            raise ValueError(f'{spec["label"]}原文无效')
        payload = _json(raw)
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise ValueError(f'{spec["label"]}结构无效')
        metadata, data = payload[0].get('metadata'), payload[0].get('data')
        total = metadata.get('recordcount') if isinstance(metadata, dict) else None
        declared_pages = metadata.get('pagecount') if isinstance(metadata, dict) else None
        if (type(total) is not int or not 0 < total <= 20000 or type(declared_pages) is not int
                or not 1 <= declared_pages <= 1000 or not isinstance(data, list)):
            raise ValueError(f'{spec["label"]}总数或分页无效')
        if expected is None:
            expected = (total, declared_pages)
        elif expected != (total, declared_pages):
            raise ValueError(f'{spec["label"]}总数或页数变化')
        ref = _page_ref(root, f'{key}-{page:03d}.json', raw)
        refs.append({**ref, 'page': page, 'count': len(data)})
        raw_count += len(data)
        rows.extend(normalized for row in data if isinstance(row, dict)
                    if (normalized := _normalize(key, row, ref['sha256'])) is not None)
        page_count = declared_pages
        if page >= page_count:
            break
        page += 1
    if expected is None or raw_count != expected[0] or len(refs) != page_count:
        raise ValueError(f'{spec["label"]}分页不完整')
    dates = sorted(row['listed_on'] for row in rows if row.get('listed_on'))
    source = {'label': spec['label'], 'url': SZSE_URL,
              'params': {'catalog': spec['catalog'], 'tabkey': spec['tabkey']},
              'declared_total': expected[0], 'page_count': page_count, 'raw_received': raw_count, 'parsed': len(rows),
              'dropped': raw_count - len(rows), 'received': len(rows), 'pages': refs,
              'date_range': [dates[0], dates[-1]] if dates else None}
    return source, rows


def _index(records: list[dict[str, Any]], as_of: str, sources: dict[str, Any],
           dropped: int) -> dict[str, Any]:
    symbols: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    unmatched = 0
    for record in records:
        identity = (record['source'], str(record['document_id']))
        if identity in seen:
            raise ValueError('监管列表文书重复')
        seen.add(identity)
        if record['code'] is None:
            unmatched += 1
            continue
        symbols.setdefault(record['code'], []).append(record)
    for rows in symbols.values():
        rows.sort(key=lambda row: (row.get('listed_on') or '', row['document_id']), reverse=True)
    dates = sorted(row['listed_on'] for row in records if row.get('listed_on'))
    return {'version': VERSION, 'as_of': as_of, 'sources': sources, 'symbols': symbols,
            'records': sum(len(rows) for rows in symbols.values()), 'unmatched': unmatched,
            'invalid': dropped, 'date_range': [dates[0], dates[-1]] if dates else None}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def _parse_page(source: str, ref: dict[str, Any], raw: bytes) -> tuple[int, tuple[dict[str, Any], ...]]:
    key = (source, ref['sha256'])
    cached = _PAGE_CACHE.get(key)
    if cached is not None:
        return cached
    if source == 'sse':
        payload = _jsonp(raw)
        rows = payload['pageHelp']['data']
    else:
        payload = _json(raw)
        rows = payload[0]['data']
    if not isinstance(rows, list) or len(rows) != ref.get('count'):
        raise ValueError('监管列表分页条数与记录不一致')
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        record = _normalize(source, row, ref['sha256'])
        if record is not None:
            normalized.append(record)
    result = (len(rows), tuple(normalized))
    if len(_PAGE_CACHE) >= _PAGE_CACHE_LIMIT:
        _PAGE_CACHE.pop(next(iter(_PAGE_CACHE)))
    _PAGE_CACHE[key] = result
    return result


def _read_pages(root: Path, manifest: dict[str, Any]) -> tuple[list[dict[str, Any]],
                                                              dict[str, dict[str, int]]]:
    records: list[dict[str, Any]] = []
    stats: dict[str, dict[str, int]] = {}
    for key, source in manifest['sources'].items():
        page_count = source.get('page_count')
        if type(page_count) is not int or page_count != len(source['pages']):
            raise ValueError('监管列表登记页数与声明总页数不一致')
        raw_received = 0
        parsed = 0
        for ref in source['pages']:
            _, raw = _reference(root, ref, need_size=True)
            raw_count, rows = _parse_page(key, ref, raw)
            if raw_count != ref.get('count'):
                raise ValueError('监管列表分页条数与记录不一致')
            raw_received += raw_count
            parsed += len(rows)
            records.extend(rows)
        dropped = raw_received - parsed
        if raw_received != source.get('declared_total'):
            raise ValueError('监管列表实际条数与声明总数不一致')
        if (source.get('raw_received') != raw_received or source.get('parsed') != parsed
                or source.get('dropped') != dropped or source.get('received') != parsed):
            raise ValueError('监管列表来源统计与原文不一致')
        stats[key] = {'raw_received': raw_received, 'parsed': parsed, 'dropped': dropped}
    return records, stats


def _build_index(manifest: dict[str, Any], records: list[dict[str, Any]],
                 stats: dict[str, dict[str, int]]) -> dict[str, Any]:
    dropped = sum(value['dropped'] for value in stats.values())
    return _index(records, manifest['as_of'], manifest['sources'], dropped)


def _snapshot(state_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, int]]]:
    """从原文重建索引并与保存索引逐条比较；失败即不可用。"""
    data = _validate_manifest(state_dir)
    records, stats = _read_pages(data['root'], data['manifest'])
    rebuilt = _build_index(data['manifest'], records, stats)
    if _canonical(rebuilt) != _canonical(data['index']):
        raise ValueError('监管列表索引与原文重建结果不一致')
    manifest = data['manifest']
    if (manifest.get('records') != rebuilt['records'] or manifest.get('unmatched') != rebuilt['unmatched']
            or manifest.get('invalid') != rebuilt['invalid']):
        raise ValueError('监管列表条数与原文重建结果不一致')
    if _bytes(data['manifest_path']) != data['raw_manifest']:
        raise ValueError('监管列表读取期间变化')
    _, raw_index = _reference(data['root'], data['index_ref'], need_size=True)
    if raw_index != data['raw_index']:
        raise ValueError('监管列表索引在读取期间变化')
    return data, rebuilt, stats


def collect_exchange_governance(state_dir: Path, *,
                                fetch: Callable[[str, dict[str, str], str], tuple[int, bytes]] = _fetch,
                                sleep: Callable[[float], Any] = time.sleep) -> dict[str, Any]:
    """保留四类监管列表原文；失败立即失效旧快照，不修改业务数据库。"""
    root = _root(state_dir)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / '.collect.lock'
    if lock_path.is_symlink():
        raise ValueError('监管列表锁不能是符号链接')
    try:
        with FileLock(str(lock_path), timeout=0):
            return _collect_locked(state_dir, root, fetch, sleep)
    except Timeout:
        return {'status': 'failed', 'reason': '监管列表采集锁已存在，需等待或检查中断任务',
                'research_only': True, 'formal_screening_enabled': False}


def _collect_locked(state_dir: Path, root: Path, fetch: Callable[[str, dict[str, str], str], tuple[int, bytes]],
                    sleep: Callable[[float], Any]) -> dict[str, Any]:
    manifest_path = root / 'manifest.json'
    started = datetime.now(_BJ)
    failed = {'version': VERSION, 'status': 'failed', 'as_of': started.date().isoformat()}
    _atomic(manifest_path, json.dumps(failed).encode())
    try:
        sources: dict[str, Any] = {}
        records: list[dict[str, Any]] = []
        dropped = 0
        source, rows = _collect_sse(root, fetch, sleep)
        sources['sse'] = source
        records.extend(rows)
        dropped += source['dropped']
        for key in ('szse_measure', 'szse_discipline'):
            source, rows = _collect_szse(root, key, SZSE_SOURCES[key], fetch, sleep)
            sources[key] = source
            records.extend(rows)
            dropped += source['dropped']
        as_of = datetime.now(_BJ)
        if as_of.date() != started.date():
            raise ValueError('监管列表采集跨日期')
        index = _index(records, as_of.date().isoformat(), sources, dropped)
        raw = json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        index_ref = _page_ref(root, 'index.json', raw)
        manifest = {'version': VERSION, 'status': 'complete', 'as_of': as_of.date().isoformat(),
                    'fetched_at': as_of.isoformat(), 'sources': sources, 'index': index_ref,
                    'records': index['records'], 'unmatched': index['unmatched'],
                    'invalid': index['invalid']}
        _atomic(manifest_path, json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode())
        verified = verify_exchange_governance(state_dir, _allow_locked=True)
        if verified['status'] != 'current':
            raise ValueError('监管列表原文复读失败')
        return {'status': 'complete', 'as_of': as_of.date().isoformat(), 'records': index['records'],
                'unmatched': index['unmatched'], 'invalid': index['invalid'],
                'sources': {key: {'declared_total': value['declared_total'], 'received': value['received'],
                                  'dropped': value['dropped']} for key, value in sources.items()},
                'research_only': True, 'formal_screening_enabled': False}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError,
            json.JSONDecodeError, ProviderRequestError, requests.RequestException) as exc:
        logger.warning('监管列表采集失败: %s', exc)
        _atomic(manifest_path, json.dumps(failed).encode())
        return {**failed, 'reason': str(exc), 'research_only': True, 'formal_screening_enabled': False}


def _validate_manifest(state_dir: Path) -> dict[str, Any]:
    root = _root(state_dir)
    manifest_path = _path(root, 'manifest.json')
    raw_manifest = _bytes(manifest_path)
    manifest = _json(raw_manifest)
    if manifest.get('version') != VERSION or manifest.get('status') != 'complete':
        raise ValueError('监管列表登记无效')
    captured = datetime.fromisoformat(manifest['fetched_at'])
    snapshot_day = date.fromisoformat(manifest['as_of'])
    if (captured.utcoffset() is None or captured > datetime.now(UTC)
            or captured.astimezone(_BJ).date() != snapshot_day):
        raise ValueError('监管列表时间无效')
    index_ref = manifest.get('index')
    _, raw_index = _reference(root, index_ref, need_size=True)
    index = _json(raw_index)
    if index.get('version') != VERSION or index.get('as_of') != manifest['as_of']:
        raise ValueError('监管列表索引无效')
    if (index.get('records') != manifest.get('records') or index.get('invalid') != manifest.get('invalid')
            or index.get('unmatched') != manifest.get('unmatched')):
        raise ValueError('监管列表条数不一致')
    symbols = index.get('symbols')
    if not isinstance(symbols, dict):
        raise TypeError('监管列表索引结构无效')
    sources = manifest.get('sources')
    if not isinstance(sources, dict) or set(sources) != SOURCE_KEYS:
        raise ValueError('监管列表来源集合无效')
    for key, source in sources.items():
        expected_url = SSE_URL if key == 'sse' else SZSE_URL
        expected_params = SSE_PARAMS if key == 'sse' else {'catalog': SZSE_SOURCES[key]['catalog'],
                                                          'tabkey': SZSE_SOURCES[key]['tabkey']}
        if (not isinstance(source, dict) or source.get('url') != expected_url
                or source.get('params') != expected_params or not isinstance(source.get('pages'), list)
                or not source['pages'] or type(source.get('page_count')) is not int
                or source['page_count'] != len(source['pages'])):
            raise ValueError('监管列表来源描述无效')
    if index.get('sources') != sources:
        raise ValueError('监管列表来源元数据与索引不一致')
    return {'root': root, 'manifest_path': manifest_path, 'raw_manifest': raw_manifest,
            'raw_index': raw_index, 'manifest': manifest, 'index': index, 'index_ref': index_ref,
            'manifest_sha256': hashlib.sha256(raw_manifest).hexdigest(),
            'index_sha256': index_ref['sha256']}


def rebuild_exchange_governance_index(state_dir: Path, *, _allow_locked: bool = False) -> dict[str, Any]:
    """从保存的原始分页重建派生索引；保留真实抓取与快照日期。"""
    root = _root(state_dir)
    lock_path = root / '.collect.lock'
    try:
        if _allow_locked:
            return _rebuild_locked(state_dir)
        with FileLock(str(lock_path), timeout=3):
            return _rebuild_locked(state_dir)
    except (Timeout, OSError):
        return {'status': 'failed', 'reason': '监管列表重建锁不可用'}


def _rebuild_locked(state_dir: Path) -> dict[str, Any]:
    root = _root(state_dir)
    manifest_path = _path(root, 'manifest.json')
    raw_manifest = _bytes(manifest_path)
    manifest = _json(raw_manifest)
    version = manifest.get('version')
    if version == VERSION:
        verified = verify_exchange_governance(state_dir, _allow_locked=True)
        return {**verified, 'status': 'current' if verified['status'] == 'current' else 'failed',
                'reason': None if verified['status'] == 'current' else '现有索引未通过复核'}
    try:
        if version not in LEGACY_VERSIONS or manifest.get('status') != 'complete':
            raise ValueError('监管列表版本或状态不能重建')
        captured = datetime.fromisoformat(manifest['fetched_at'])
        snapshot_day = date.fromisoformat(manifest['as_of'])
        if (captured.utcoffset() is None or captured > datetime.now(UTC)
                or captured.astimezone(_BJ).date() != snapshot_day):
            raise ValueError('监管列表时间无效')
        sources = {}
        for key, value in manifest['sources'].items():
            if not isinstance(value, dict) or not isinstance(value.get('pages'), list) or not value['pages']:
                raise ValueError('监管列表来源描述无效')
            sources[key] = {**value, 'page_count': value.get('page_count', len(value['pages']))}
        working = {**manifest, 'sources': sources}
        records, stats = _read_pages(root, working)
        index = _build_index(working, records, stats)
        raw_index = _canonical(index)
        index_ref = _page_ref(root, 'index.json', raw_index)
        new_manifest = {**working, 'version': VERSION, 'index': index_ref,
                        'records': index['records'], 'unmatched': index['unmatched'],
                        'invalid': index['invalid'], 'rebuilt_at': datetime.now(_BJ).isoformat(),
                        'rebuilt_from_version': version}
        _atomic(manifest_path, json.dumps(new_manifest, ensure_ascii=False, sort_keys=True).encode())
        verified = verify_exchange_governance(state_dir, _allow_locked=True)
        if verified['status'] != 'current':
            raise ValueError('重建后的监管列表未通过复核')
        return {'status': 'rebuilt', 'as_of': manifest['as_of'], 'fetched_at': manifest['fetched_at'],
                'rebuilt_at': new_manifest['rebuilt_at'], 'from_version': version,
                'records': index['records'], 'unmatched': index['unmatched'], 'invalid': index['invalid']}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, json.JSONDecodeError) as exc:
        logger.warning('监管列表重建失败: %s', exc)
        return {'status': 'failed', 'reason': str(exc)}


def governance_snapshot_identity(state_dir: Path) -> dict[str, Any] | None:
    """轻量读取当前登记身份，用于同一详情请求内的快照一致性复核。"""
    try:
        data = _validate_manifest(state_dir)
        return {'manifest_sha256': data['manifest_sha256'], 'index_sha256': data['index_sha256'],
                'as_of': data['manifest']['as_of'], 'fetched_at': data['manifest']['fetched_at']}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, json.JSONDecodeError) as exc:
        logger.debug('监管列表身份不可用: %s', exc)
        return None


def verify_exchange_governance(state_dir: Path, *, _allow_locked: bool = False) -> dict[str, Any]:
    """复核 manifest、索引和全部原始分页；供采集和离线盘点使用。"""
    root = _root(state_dir)
    lock_path = root / '.collect.lock'
    try:
        if _allow_locked:
            return _verify_locked(state_dir)
        with FileLock(str(lock_path), timeout=3):
            return _verify_locked(state_dir)
    except (Timeout, OSError):
        return {'status': 'unavailable', 'as_of': None, 'records': 0, 'sources': {}}


def _verify_locked(state_dir: Path) -> dict[str, Any]:
    try:
        data, index, _stats = _snapshot(state_dir)
        manifest = data['manifest']
        return {'status': 'current', 'as_of': manifest['as_of'], 'records': index['records'],
                'unmatched': index['unmatched'], 'invalid': index['invalid'],
                'sources': {key: value['received'] for key, value in manifest['sources'].items()},
                'verified_pages': sum(len(value['pages']) for value in manifest['sources'].values()),
                'manifest_sha256': data['manifest_sha256']}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, json.JSONDecodeError) as exc:
        logger.debug('监管列表不可用: %s', exc)
        return {'status': 'unavailable', 'as_of': None, 'records': 0, 'sources': {}}


def load_exchange_governance_source(state_dir: Path, symbol: str, as_of: date) -> dict[str, Any]:
    """按公司读取监管列表命中；未命中不代表治理合格。"""
    symbol = _symbol(symbol)
    if type(as_of) is not date:
        raise ValueError('研究截点无效')
    out: dict[str, Any] = {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'unavailable',
                           'observation': 'unknown', 'records': [], 'reviewed': False,
                           'formal_screening_enabled': False}
    root = _root(state_dir)
    lock_path = root / '.collect.lock'
    try:
        with FileLock(str(lock_path), timeout=3):
            return _load_locked(state_dir, symbol, as_of, out)
    except (Timeout, OSError):
        return out


def _load_locked(state_dir: Path, symbol: str, as_of: date, out: dict[str, Any]) -> dict[str, Any]:
    try:
        data, index, _stats = _snapshot(state_dir)
        manifest = data['manifest']
        snapshot_day = date.fromisoformat(manifest['as_of'])
        if snapshot_day > as_of:
            raise ValueError('监管列表晚于研究截点')
        rows = index['symbols'].get(symbol, [])
        if not isinstance(rows, list):
            raise TypeError('监管列表公司记录无效')
        if any(row.get('code') != symbol for row in rows):
            raise ValueError('监管列表命中与查询公司不一致')
        out.update(status='current' if snapshot_day == as_of else 'historical',
                   observation='observed' if rows else 'not_observed',
                   records=[{key: row.get(key) for key in ('source', 'document_id', 'listed_on', 'measure_type',
                                                           'measure_category', 'object', 'title', 'url',
                                                           'page_sha256')} for row in rows],
                   snapshot_date=snapshot_day.isoformat(), fetched_at=manifest['fetched_at'],
                   manifest_sha256=data['manifest_sha256'], index_sha256=data['index_sha256'],
                   coverage={key: {'label': value['label'], 'params': value['params'],
                                   'declared_total': value.get('declared_total'),
                                   'raw_received': value.get('raw_received'), 'parsed': value.get('parsed'),
                                   'received': value.get('received'), 'dropped': value.get('dropped'),
                                   'page_count': value.get('page_count'), 'pages': len(value.get('pages', [])), 'date_range': value.get('date_range'),
                                   'snapshot_date': snapshot_day.isoformat()}
                             for key, value in manifest['sources'].items()},
                   reason='仅为交易所动态列表观察结果；未命中不代表治理合格，工作函不等同处分')
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, json.JSONDecodeError) as exc:
        logger.debug('监管列表不可用: %s', exc)
    return out

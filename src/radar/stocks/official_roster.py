"""官方沪深当前候选名录；原始行业和上市日仅为待审核线索。"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import stat
import tempfile
import zipfile
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from time import sleep as _sleep
from typing import Any
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

import requests

from radar.provider_request import ProviderRequestError, bounded_provider_call
from radar.stocks.admission import _BJ, _day
from radar.stocks.research_store import StockResearchError, _symbol
from radar.stocks.source_archive import _close

logger = logging.getLogger(__name__)
VERSION = 'official-stock-roster-v1'
_MAX_BODY = 4*1024*1024
_SQL_ID = 'COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L'
_BOARDS = {'sse_main': ('1', '1'), 'sse_star': ('8', '2')}


def _now() -> datetime:
    return datetime.now(UTC)


def _url(source: str) -> str:
    if source == 'szse_a':
        return 'https://www.szse.cn/api/report/ShowReport?' + urlencode({'SHOWTYPE': 'xlsx', 'CATALOGID': '1110', 'TABKEY': 'tab1'})
    stock_type, _board = _BOARDS[source]
    parameters = {'STOCK_TYPE': stock_type, 'REG_PROVINCE': '', 'CSRC_CODE': '', 'STOCK_CODE': '', 'sqlId': _SQL_ID,
                  'COMPANY_STATUS': '2,4,5,7,8', 'type': 'inParams', 'isPagination': 'true', 'pageHelp.cacheSize': '1',
                  'pageHelp.beginPage': '1', 'pageHelp.pageSize': '10000', 'pageHelp.pageNo': '1', 'pageHelp.endPage': '1'}
    return 'https://query.sse.com.cn/sseQuery/commonQuery.do?' + urlencode(parameters)


def _result() -> dict[str, Any]:
    return {'rows': [], 'declared_total': None, 'row_count': 0, 'scope_exclusions': [], 'diagnostics': [], 'complete': False}


def _listing(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time().isoformat() == '00:00:00' else None
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, str) and re.fullmatch(r'\d{8}', value):
        value = f'{value[:4]}-{value[4:6]}-{value[6:]}'
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T00:00:00', value):
        value = value[:10]
    parsed = _day(value)
    return parsed.isoformat() if parsed else None


def _deduplicate(result: dict[str, Any], rows: list[dict[str, Any]], counts: dict[str, int]) -> bool:
    duplicates = {symbol for symbol, count in counts.items() if count > 1}
    result['rows'] = [row for row in rows if row['symbol'] not in duplicates]
    for symbol in sorted(duplicates):
        result['diagnostics'].append(f'{symbol}重复名录行全部待核验，未选择版本')
    return not duplicates


def _reject_constant(value: str) -> Any:
    raise ValueError(f'官方JSON含非标准非有限数值{value}')


def parse_sse_roster(raw: bytes, board: str = 'sse_main') -> dict[str, Any]:
    """只在result完整覆盖源声明总数且身份唯一时认定该次来源完整。"""
    if board not in _BOARDS:
        raise ValueError('不支持的上交所板块')
    if not isinstance(raw, bytes) or len(raw) > _MAX_BODY:
        raise ValueError('上交所名录响应超预算或类型无效')
    result = _result()
    payload = json.loads(raw.decode('utf-8-sig'), parse_constant=_reject_constant)
    if not isinstance(payload, dict) or payload.get('sqlId') != _SQL_ID or not isinstance(payload.get('result'), list):
        result['diagnostics'].append('上交所sqlId、结果类型或错误包未通过校验')
        return result
    if payload.get('actionErrors') or payload.get('fieldErrors'):
        result['diagnostics'].append('上交所响应带有查询错误，不能认定名录完整')
        return result
    records = payload['result']
    result['row_count'] = len(records)
    metadata = payload.get('pageHelp')
    total = metadata.get('total') if isinstance(metadata, dict) else None
    valid = type(total) is int and 0 < total <= 20000 and len(records) == total
    result['declared_total'] = total if type(total) is int else None
    if not valid:
        result['diagnostics'].append('源声明总数缺失、异常或本次result未覆盖全部总数')
    stock_type, list_board = _BOARDS[board]
    rows = []
    counts: dict[str, int] = {}
    expected = r'60[0135]\d{3}' if board == 'sse_main' else r'688\d{3}'
    for record in records:
        if not isinstance(record, dict):
            result['diagnostics'].append('存在非对象名录行')
            valid = False
            continue
        symbol = record.get('A_STOCK_CODE')
        if isinstance(symbol, str) and re.fullmatch(expected, symbol):
            counts[symbol] = counts.get(symbol, 0)+1
        try:
            json.dumps(record, allow_nan=False)
        except (TypeError, ValueError, OverflowError, RecursionError):
            result['diagnostics'].append(f'{symbol}原始字段不能严格导出')
            valid = False
            continue
        if record.get('STOCK_TYPE') != stock_type or record.get('LIST_BOARD') != list_board:
            result['diagnostics'].append(f'{symbol}股票类型或板块身份不匹配')
            valid = False
            continue
        if board == 'sse_star' and symbol == '689009':
            result['scope_exclusions'].append({'symbol': symbol, 'reason': '明确为CDR，不属于普通A股范围', 'source_fields': deepcopy(record)})
            continue
        try:
            _symbol(symbol)
            if not isinstance(symbol, str) or re.fullmatch(expected, symbol) is None:
                raise StockResearchError('板块证券代码范围无效')
        except StockResearchError:
            result['diagnostics'].append(f'{symbol}为未知或非对应市场普通A股身份，未静默排除')
            valid = False
            continue
        if record.get('COMPANY_CODE') != symbol:
            result['diagnostics'].append(f'{symbol}公司代码与证券身份不一致')
            valid = False
            continue
        name = record.get('SEC_NAME_CN') or record.get('COMPANY_ABBR')
        if not isinstance(name, str) or not name.strip():
            result['diagnostics'].append(f'{symbol}缺少证券简称')
            valid = False
            continue
        rows.append({'symbol': symbol, 'name': name.strip(), 'a_listed_on': _listing(record.get('LIST_DATE')),
                     'industry_source_code': record.get('CSRC_CODE'), 'industry_source_label': record.get('CSRC_CODE_DESC'),
                     'source_fields': deepcopy(record)})
    unique = _deduplicate(result, rows, counts)
    # CDR本身也须唯一，不能通过重复范围排除凑足总数。
    exclusions = [row['symbol'] for row in result['scope_exclusions']]
    exclusions_unique = len(set(exclusions)) == len(exclusions)
    if not exclusions_unique:
        result['diagnostics'].append('范围排除证券重复，无法确认完整性')
    result['complete'] = bool(valid and unique and exclusions_unique)
    return result


def _zip_budget(raw: bytes) -> None:
    if not isinstance(raw, bytes) or len(raw) > _MAX_BODY:
        raise ValueError('深交所XLSX压缩响应超过4MiB或类型无效')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 64 or sum(entry.file_size for entry in entries) > 20*1024*1024:
                raise ValueError('XLSX条目或展开字节超过预算')
            names = set()
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (entry.flag_bits & 1 or path.is_absolute() or '..' in path.parts or '\\' in entry.filename
                        or ':' in entry.filename or entry.filename in names or stat.S_ISLNK(entry.external_attr >> 16)):
                    raise ValueError('XLSX含加密、重复条目或异常路径')
                names.add(entry.filename)
                if entry.filename.startswith('xl/worksheets/') and entry.filename.endswith('.xml'):
                    with archive.open(entry) as handle:
                        worksheet = handle.read(20*1024*1024+1)
                    if len(worksheet) > 20*1024*1024 or b'<!DOCTYPE' in worksheet or b'<!ENTITY' in worksheet:
                        raise ValueError('工作表XML超过预算或含不允许的声明')
                    for _event, element in ElementTree.iterparse(io.BytesIO(worksheet), events=('start',)):
                        if element.tag.rsplit('}', 1)[-1] != 'c':
                            continue
                        coordinate = element.get('r')
                        match = re.fullmatch(r'([A-Z]+)([1-9]\d*)', coordinate) if isinstance(coordinate, str) else None
                        if match is None or len(match.group(1)) > 2 or len(match.group(2)) > 5:
                            raise ValueError('工作表单元格坐标无效或超过预算')
                        column = 0
                        for letter in match.group(1):
                            column = column*26 + ord(letter)-64
                        if column > 64 or int(match.group(2)) > 20000:
                            raise ValueError('工作表实际单元格超过20000行或64列预算')
                        element.clear()
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError('深交所响应不是有效XLSX包或工作表XML') from exc


def parse_szse_roster(raw: bytes) -> dict[str, Any]:
    """严格读取A股列表，错误dimension不截断行，公式和代码数值不转事实。"""
    from openpyxl import load_workbook

    _zip_budget(raw)
    result = _result()
    workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    try:
        if 'A股列表' not in workbook.sheetnames:
            result['diagnostics'].append('未找到明确A股列表工作表')
            return result
        sheet = workbook['A股列表']
        sheet.reset_dimensions()
        iterator = sheet.iter_rows(max_col=65, max_row=20001)
        header_cells = next(iterator, ())
        if not header_cells:
            result['diagnostics'].append('A股列表为空')
            return result
        headers = [cell.value.strip() if isinstance(cell.value, str) else cell.value for cell in header_cells]
        used = [header for header in headers if header is not None]
        required = {'A股代码', 'A股简称', 'A股上市日期', '所属行业'}
        if (headers[64] is not None or any(cell.data_type == 'f' for cell in header_cells)
                or any(not isinstance(header, str) or not header for header in used)
                or len(set(used)) != len(used) or not required.issubset(used)):
            result['diagnostics'].append('必需表头缺失、重复、公式或列预算超过64')
            return result
        valid, rows = True, []
        counts: dict[str, int] = {}
        for index, cells in enumerate(iterator, 2):
            if all(cell.value is None for cell in cells):
                continue
            result['row_count'] += 1
            if index > 20000 or cells[64].value is not None:
                result['diagnostics'].append('工作表行/列超过20000/64预算')
                valid = False
                break
            raw_record = {header: cell.value for header, cell in zip(headers[:64], cells[:64], strict=True) if isinstance(header, str)}
            symbol = raw_record.get('A股代码')
            if isinstance(symbol, str) and re.fullmatch(r'(?:000|001|002|003|30[012])\d{3}', symbol):
                counts[symbol] = counts.get(symbol, 0)+1
            if any(cell.data_type == 'f' for cell in cells):
                result['diagnostics'].append(f'第{index}行含公式，未作为名录事实')
                valid = False
                continue
            listed = _listing(raw_record.get('A股上市日期'))
            record = {header: (value.isoformat() if isinstance(value, (date, datetime)) else value)
                      for header, value in raw_record.items()}
            try:
                _symbol(symbol)
                if not isinstance(symbol, str) or re.fullmatch(r'(?:000|001|002|003|30[012])\d{3}', symbol) is None:
                    raise StockResearchError('深交所六位字符串代码无效')
            except StockResearchError:
                result['diagnostics'].append(f'第{index}行代码非明确深市六位字符串，不补零')
                valid = False
                continue
            name = record.get('A股简称')
            if not isinstance(name, str) or not name.strip():
                result['diagnostics'].append(f'{symbol}缺少证券简称')
                valid = False
                continue
            try:
                json.dumps(record, allow_nan=False)
            except (TypeError, ValueError, OverflowError, RecursionError):
                result['diagnostics'].append(f'{symbol}原始表格字段不能严格导出')
                valid = False
                continue
            industry = record.get('所属行业')
            match = re.fullmatch(r'([A-Z])\s+(.+)', industry) if isinstance(industry, str) else None
            rows.append({'symbol': symbol, 'name': name.strip(), 'a_listed_on': listed,
                         'industry_source_code': match.group(1) if match else None,
                         'industry_source_label': match.group(2) if match else industry, 'source_fields': record})
        unique = _deduplicate(result, rows, counts)
        result['complete'] = bool(valid and unique and result['row_count'] > 0)
        return result
    finally:
        workbook.close()


def _parse_source(raw: bytes, source: str) -> dict[str, Any]:
    return parse_szse_roster(raw) if source == 'szse_a' else parse_sse_roster(raw, source)


def _headers(url: str) -> dict[str, str]:
    # 沪市需要公开页面Referer；深市按其现有SDK契约使用默认请求头。
    host = urlsplit(url).hostname
    if host == 'query.sse.com.cn':
        return {'Referer': 'https://www.sse.com.cn/assortment/stock/list/share/', 'User-Agent': 'Mozilla/5.0'}
    if host == 'www.szse.cn':
        return {}
    raise ValueError('不支持的官方名录来源主机')


def _download_once(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=15, stream=True, allow_redirects=False,
                            headers=_headers(url))
    try:
        status = response.status_code
        if status != 200:
            return {'status': status, 'raw': b'', 'error': None}
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            if not isinstance(chunk, bytes):
                return {'status': status, 'raw': b'', 'error': '原始响应流类型无效'}
            size += len(chunk)
            if size > _MAX_BODY:
                return {'status': status, 'raw': b'', 'error': '名录原始响应超过4MiB'}
            chunks.append(chunk)
        return {'status': status, 'raw': b''.join(chunks), 'error': None}
    finally:
        _close(response)


def _atomic(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise ValueError('缓存文件不能是符号链接')
    descriptor, name = tempfile.mkstemp(prefix='.roster-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _cache(root: Path, manifest: Path, source: str, cutoff: date, now: datetime) -> tuple[bytes, dict[str, Any]] | None:
    try:
        if not manifest.is_file():
            return None
        with manifest.open('rb') as handle:
            manifest_raw = handle.read(65537)
        if len(manifest_raw) > 65536:
            return None
        record = json.loads(manifest_raw.decode('utf-8'))
        if not isinstance(record, dict) or record.get('status') != 'downloaded' or record.get('url') != _url(source):
            return None
        sha = record.get('content_sha256')
        if not isinstance(sha, str) or re.fullmatch('[0-9a-f]{64}', sha) is None:
            return None
        fetched = datetime.fromisoformat(record['fetched_at'])
        if fetched.tzinfo is None or fetched.utcoffset() is None or fetched > now or fetched.astimezone(_BJ).date() != cutoff:
            return None
        path = root/f'{sha}.bin'
        if path.is_symlink() or not path.is_file():
            return None
        with path.open('rb') as handle:
            raw = handle.read(_MAX_BODY+1)
        if not 0 < len(raw) <= _MAX_BODY or hashlib.sha256(raw).hexdigest() != sha:
            return None
        return raw, record
    except (ValueError, TypeError, KeyError, OSError):
        logger.warning('官方名录缓存不可用，按日期限制重新获取: %s', manifest)
        return None


def _soft_budget(root: Path) -> list[str]:
    total, warnings = 0, []
    try:
        for path in root.iterdir():
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError as exc:
                logger.warning('官方名录软预算跳过无法统计文件 %s: %s', path, exc)
                warnings.append('部分缓存大小无法统计')
    except OSError as exc:
        logger.warning('官方名录软预算目录统计失败 %s: %s', root, exc)
        warnings.append('缓存目录无法统计，未自动删除')
    if total > 1024**3:
        warnings.append('缓存超过1GiB软上限，未自动删除')
    return list(dict.fromkeys(warnings))


def collect_stock_roster(cache_dir: Path, *, as_of: date, refresh: bool = False,
                         http_get: Callable[..., Any] | None = None, sleep: Callable[[float], Any] = _sleep) -> dict[str, Any]:
    """当前官方名单采集或同日缓存复用；历史日期无缓存时不伪造历史名单。"""
    now, cutoff = _now(), _day(as_of)
    if (now.tzinfo is None or now.utcoffset() is None or cutoff is None or cutoff > now.astimezone(_BJ).date()):
        raise ValueError('采集时间或研究日期无效/未来')
    root = Path(cache_dir).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)) or (root.exists() and any(path.is_symlink() for path in root.iterdir())):
        raise ValueError('缓存目录或文件不得经过符号链接')
    root.mkdir(parents=True, exist_ok=True)
    items, stopped, attempted = [], set(), False
    for source in ('sse_main', 'sse_star', 'szse_a'):
        url, host = _url(source), urlsplit(_url(source)).hostname
        key = hashlib.sha256(json.dumps({'version': VERSION, 'source': source, 'as_of': cutoff.isoformat()}, sort_keys=True).encode()).hexdigest()
        manifest = root/f'{key}.json'
        item: dict[str, Any] = {'source': source, 'url': url, 'status': 'source_error', 'complete': False}
        raw, fetched_at = None, None
        try:
            cached = _cache(root, manifest, source, cutoff, now) if not refresh else None
            if cached is not None:
                raw, metadata = cached
                fetched_at, item['status'] = metadata['fetched_at'], 'cached'
            if raw is None:
                if host in stopped:
                    item['status'] = 'source_stopped'
                elif cutoff != now.astimezone(_BJ).date():
                    raise ValueError('当前接口没有历史成员能力，缺少研究日当日抓取缓存，未联网')
                else:
                    for attempt in range(3):
                        response = None
                        try:
                            if attempted:
                                sleep((3 if attempt == 1 else 10) if attempt else 1)
                            attempted = True
                            if http_get is None:
                                downloaded = bounded_provider_call(__name__, '_download_once', args=(url,), timeout=15)
                            else:
                                response = http_get(url, timeout=15, stream=True, allow_redirects=False,
                                                    headers=_headers(url))
                                downloaded = {'status': response.status_code, 'raw': b'', 'error': None}
                                if response.status_code == 200:
                                    chunks, size = [], 0
                                    for chunk in response.iter_content(chunk_size=65536):
                                        if not isinstance(chunk, bytes):
                                            raise TypeError('原始响应流类型无效')
                                        size += len(chunk)
                                        if size > _MAX_BODY:
                                            raise ValueError('名录原始响应超过4MiB')
                                        chunks.append(chunk)
                                    downloaded['raw'] = b''.join(chunks)
                            status = downloaded['status']
                            if downloaded['error'] is not None:
                                raise ValueError(downloaded['error'])
                            if status == 429:
                                stopped.add(host)
                                raise ValueError('来源429，停止该主机后续请求')
                            if status >= 500:
                                raise requests.RequestException(f'HTTP{status}')
                            if status != 200:
                                raise ValueError(f'HTTP{status}不接受非成功响应或重定向')
                            raw = downloaded['raw']
                            fetched_at = _now().astimezone(UTC).isoformat()
                            sha = hashlib.sha256(raw).hexdigest()
                            item.update(content_sha256=sha, fetched_at=fetched_at, raw_artifact=f'{sha}.bin')
                            _atomic(root/f'{sha}.bin', raw)
                            item['status'] = 'downloaded'
                            break
                        except (requests.RequestException, OSError, ProviderRequestError):
                            if attempt == 2:
                                raise
                            logger.warning('官方名录网络失败，按预算重试: %s', source)
                        finally:
                            if response is not None:
                                _close(response)
            if raw is not None:
                sha = hashlib.sha256(raw).hexdigest()
                item.update(content_sha256=sha, fetched_at=fetched_at, raw_artifact=f'{sha}.bin')
                parsed = (bounded_provider_call(__name__, '_parse_source', args=(raw, source), timeout=15)
                          if http_get is None else _parse_source(raw, source))
                item.update(**parsed)
                if item['status'] == 'downloaded':
                    _atomic(manifest, json.dumps({'status': 'downloaded', 'url': url, 'content_sha256': sha,
                                                  'fetched_at': fetched_at}, ensure_ascii=False).encode())
        except Exception as exc:  # noqa: BLE001 各官方来源独立隔离网络、解析和缓存失败
            logger.warning('官方名录采集失败 %s: %s', source, exc)
            item.update(status='source_error', error=str(exc), complete=False)
            try:
                _atomic(manifest, json.dumps({'status': 'source_error', 'url': url, 'error': str(exc),
                    'content_sha256': item.get('content_sha256'), 'fetched_at': item.get('fetched_at')}, ensure_ascii=False).encode())
            except (OSError, ValueError) as cache_exc:
                logger.warning('官方名录失败记录未保存 %s: %s', source, cache_exc)
        items.append(item)
    companies: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    diagnostics = []
    for item in items:
        if item['status'] not in ('downloaded', 'cached'):
            continue
        for row in item.get('rows', []):
            symbol = row['symbol']
            if symbol in companies or symbol in conflicts:
                companies.pop(symbol, None)
                conflicts.add(symbol)
                diagnostics.append(f'{symbol}跨来源身份重复或冲突，全部待核验')
                continue
            hint = {'source': item['source'], 'source_url': item['url'], 'content_sha256': item['content_sha256'],
                    'fetched_at': item['fetched_at'], **deepcopy(row)}
            exchange = 'SZSE' if item['source'] == 'szse_a' else 'SSE'
            companies[symbol] = {'symbol': symbol, 'name': row['name'], 'admission_status': 'pending_verification',
                'research_hints': [hint], 'security_proposal': {'symbol': symbol, 'kind': 'admission_security',
                'payload': {'exchange': exchange, 'security_type': 'ordinary_a', 'a_listed_on': row['a_listed_on'], 'issuer_type': None},
                'source_url': item['url'], 'content_sha256': item['content_sha256'],
                'disclosed_on': datetime.fromisoformat(item['fetched_at']).astimezone(_BJ).date().isoformat(), 'requires_review': True}}
    complete = all(item['complete'] and item['status'] in ('downloaded', 'cached') for item in items) and not conflicts
    return {'version': VERSION, 'as_of': cutoff.isoformat(), 'items': items, 'status': 'complete' if complete else 'partial',
            'financial_verified': False, 'companies': [companies[symbol] for symbol in sorted(companies)],
            'universe_complete': complete,
            'diagnostics': diagnostics, 'research_only': True, 'formal_screening_enabled': False,
            'members_published': 0, 'requires_review': True, 'warnings': _soft_budget(root)}

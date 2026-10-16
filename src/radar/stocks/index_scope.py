"""指数并集仅提供研究初筛范围，不代表盈利、龙头或正式成员资格。"""
from __future__ import annotations

import io
import re
from collections.abc import Iterable
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from radar.stocks.official_roster import _listing, _zip_budget

INDEX_SOURCES: dict[str, dict[str, Any]] = {
    code: {'code': code, 'name': name, 'count': count, 'provider': 'csi',
           'url': f'https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{code}cons.xls'}
    for code, name, count in [('000510', '中证A500', 500), ('000922', '中证红利', 100),
                              ('H30269', '红利低波', 50), ('000688', '科创50', 50)]
}
INDEX_SOURCES['399006'] = {'code': '399006', 'name': '创业板指', 'count': 100, 'provider': 'cni',
    'url': 'https://www.cnindex.com.cn/sample-detail/download-history?indexcode=399006'}
POOL_INDEX_CODES = {'dividend_value': ('000510', '000922', 'H30269'),
                    'growth_trend': ('000510', '399006', '000688')}
_CSI = ('日期Date', '指数代码 Index Code', '指数名称 Index Name',
        '成份券代码Constituent Code', '成份券名称Constituent Name', '交易所Exchange')
_CNI = ('日期', '样本代码', '样本简称', '所属行业', '总市值(亿元)', '权重（%）')
_EXCHANGES = {'SSE': ('上海证券交易所', 'Shanghai Stock Exchange'),
              'SZSE': ('深圳证券交易所', 'Shenzhen Stock Exchange'),
              'BSE': ('北京证券交易所', 'Beijing Stock Exchange')}


def _code(value: Any) -> str:
    # 数字代码只接受整数补零；原始单元格另外保留。
    if type(value) is int or (type(value) is float and value.is_integer()):
        return str(int(value)).zfill(6)
    return value.strip() if isinstance(value, str) else ''


def _exchange(symbol: str) -> str | None:
    if re.fullmatch(r'(?:60[0135]|688)\d{3}', symbol):
        return 'SSE'
    if re.fullmatch(r'(?:00[0123]|30[012])\d{3}', symbol):
        return 'SZSE'
    return None


def _records(raw: bytes) -> tuple[list[Any], list[list[Any]]]:
    if raw.startswith(b'PK'):
        from openpyxl import load_workbook

        _zip_budget(raw)
        book = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
        try:
            if len(book.worksheets) != 1:
                raise ValueError('指数工作簿必须只有一张明确成份表')
            sheet = book.worksheets[0]
            sheet.reset_dimensions()
            values = []
            for cells in sheet.iter_rows(max_row=20001, max_col=65):
                if any(cell.data_type == 'f' for cell in cells):
                    raise ValueError('指数成份含公式，不能作为名单事实')
                if cells[64].value is not None:
                    raise ValueError('指数成份列数超过64')
                row = [cell.value for cell in cells[:64]]
                if any(value is not None for value in row):
                    values.append(row)
            if len(values) > 20000:
                raise ValueError('指数成份行数超过预算')
            return (values[0], values[1:]) if values else ([], [])
        finally:
            book.close()
    if not raw.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
        raise ValueError('响应不是支持的XLS/XLSX工作簿')
    import pandas as pd

    with pd.ExcelFile(io.BytesIO(raw), engine='xlrd') as book:
        if len(book.sheet_names) != 1:
            raise ValueError('指数工作簿必须只有一张明确成份表')
        frame: Any = book.parse(sheet_name=0, header=None, dtype=object, keep_default_na=False)
    if len(frame) > 20000 or len(frame.columns) > 64:
        raise ValueError('指数成份行列超过预算')
    values = frame.values.tolist()
    return (values[0], values[1:]) if values else ([], [])


def parse_index_constituents(raw: bytes, index_code: str, as_of: date,
                             *, expected_count: int | None = None) -> dict[str, Any]:
    """严格解析官方快照；快照日期不推断实际调仓生效日。"""
    if index_code not in INDEX_SOURCES:
        raise ValueError('不支持的指数代码')
    cutoff = _listing(as_of)
    expected = INDEX_SOURCES[index_code]['count'] if expected_count is None else expected_count
    if cutoff is None or type(expected) is not int or not 0 < expected <= 20000:
        raise ValueError('研究日期或预期样本数无效')
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 4*1024*1024:
        raise ValueError('指数响应类型无效或超过4MiB')
    source = INDEX_SOURCES[index_code]
    result: dict[str, Any] = {'index_code': index_code, 'provider': source['provider'],
        'snapshot_date': None, 'effective_date': None, 'rows': [], 'scope_exclusions': [], 'row_count': 0,
        'expected_count': expected, 'complete': False, 'diagnostics': []}
    headers, values = _records(raw)
    headers = [value.strip() if isinstance(value, str) else value for value in headers]
    used = [value for value in headers if value not in (None, '')]
    required = _CSI if source['provider'] == 'csi' else _CNI
    if (any(not isinstance(value, str) for value in used) or len(set(used)) != len(used)
            or not set(required).issubset(used)):
        result['diagnostics'].append('必需列缺失、重复或无效')
        return result
    result['row_count'] = len(values)
    valid = len(values) == expected
    if not valid:
        result['diagnostics'].append(f'样本数{len(values)}不等于预期{expected}')
    seen: set[str] = set()
    duplicated: set[str] = set()
    dates: set[str] = set()
    for number, values_row in enumerate(values, 2):
        record = {key: (value.isoformat() if isinstance(value, (date, datetime)) else value)
                  for key, value in zip(headers, values_row) if isinstance(key, str) and key}
        csi = source['provider'] == 'csi'
        symbol = _code(record.get('成份券代码Constituent Code' if csi else '样本代码'))
        exchange = _exchange(symbol)
        reason = None
        if csi and symbol == '689009':
            exchange, reason = 'SSE', '已验证CDR，不属于沪深普通A股范围'
        elif csi and re.fullmatch(r'(?:[48]\d{5}|920\d{3})', symbol):
            exchange, reason = 'BSE', '已验证北交所证券，不属于沪深普通A股范围'
        name = record.get('成份券名称Constituent Name' if csi else '样本简称')
        snapshot = _listing(record.get('日期Date' if csi else '日期'))
        if symbol in seen:
            duplicated.add(symbol)
        seen.add(symbol)
        if snapshot is not None:
            dates.add(snapshot)
        row_valid = bool(exchange and isinstance(name, str) and name.strip() and snapshot and snapshot <= cutoff)
        if csi:
            row_valid = bool(row_valid and _code(record.get('指数代码 Index Code')) == index_code
                and record.get('指数名称 Index Name') == source['name']
                and exchange is not None and record.get('交易所Exchange') == _EXCHANGES[exchange][0])
            english = record.get('交易所英文名称Exchange(Eng)')
            if english is not None and exchange is not None and english != _EXCHANGES[exchange][1]:
                row_valid = False
        elif not re.fullmatch(r'30[012]\d{3}', symbol):
            row_valid = False
        if not row_valid:
            valid = False
            result['diagnostics'].append(f'第{number}行代码、名称、指数身份、交易所或快照日期无效')
            continue
        if reason is not None:
            result['scope_exclusions'].append({'symbol': symbol, 'name': str(name).strip(),
                'exchange': exchange, 'reason': reason, 'snapshot_date': snapshot,
                'effective_date': None, 'source_fields': record})
            continue
        result['rows'].append({'symbol': symbol, 'name': str(name).strip(), 'exchange': exchange,
            'snapshot_date': snapshot, 'effective_date': None, 'source_fields': record})
    if duplicated:
        valid = False
        result['rows'] = [row for row in result['rows'] if row['symbol'] not in duplicated]
        result['diagnostics'].append('重复证券全部待核验：' + ','.join(sorted(duplicated)))
    if len(dates) != 1:
        valid = False
        result['diagnostics'].append('来源快照日期缺失或不一致')
    else:
        result['snapshot_date'] = next(iter(dates))
    result['complete'] = bool(valid)
    return result


def build_index_scope(items: Iterable[dict[str, Any]], as_of: date,
                      *, pool_ids: Iterable[str] | None = None) -> dict[str, Any]:
    """按池合并已解析来源；不完整时不回退全市场，也不发布成员。"""
    cutoff = _listing(as_of)
    pools = list(POOL_INDEX_CODES if pool_ids is None else pool_ids)
    if cutoff is None or not pools or len(set(pools)) != len(pools) or any(pool not in POOL_INDEX_CODES for pool in pools):
        raise ValueError('研究日期或池标识无效')
    records = deepcopy(list(items))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in records:
        grouped.setdefault(item.get('index_code', item.get('code', '')), []).append(item)
    output: dict[str, Any] = {}
    companies: dict[str, dict[str, Any]] = {}
    exclusions: dict[tuple[str, str], dict[str, Any]] = {}
    for pool in pools:
        codes = POOL_INDEX_CODES[pool]
        complete = True
        symbols: set[str] = set()
        pool_exclusions = []
        for code in codes:
            sources = grouped.get(code, [])
            if len(sources) != 1:
                complete = False
                continue
            item = sources[0]
            snapshot = _listing(item.get('snapshot_date'))
            rows = item.get('rows', [])
            excluded = item.get('scope_exclusions', [])
            usable = (item.get('complete') is True and snapshot is not None and snapshot <= cutoff
                      and item.get('status', 'downloaded') in ('downloaded', 'cached')
                      and item.get('effective_date') is None and isinstance(rows, list)
                      and isinstance(excluded, list) and bool(rows or excluded)
                      and all(isinstance(row, dict) and isinstance(row.get('symbol'), str)
                          and _exchange(row['symbol']) in ('SSE', 'SZSE')
                          and _exchange(row['symbol']) == row.get('exchange')
                          and isinstance(row.get('name'), str) and bool(row['name'].strip())
                          and row.get('snapshot_date') == snapshot and row.get('effective_date') is None
                          and isinstance(row.get('source_fields'), dict) for row in rows)
                      and all(isinstance(row, dict) and isinstance(row.get('symbol'), str)
                          and bool(row.get('reason')) and isinstance(row.get('source_fields'), dict)
                          and ((row['symbol'] == '689009' and row.get('exchange') == 'SSE')
                               or (re.fullmatch(r'(?:[48]\d{5}|920\d{3})', row['symbol'])
                                   and row.get('exchange') == 'BSE'))
                          and row.get('snapshot_date') == snapshot for row in excluded)
                      and len(rows) + len(excluded) == item.get('expected_count') == item.get('row_count')
                      and len({row.get('symbol') for row in rows + excluded}) == len(rows) + len(excluded))
            if not usable:
                complete = False
                continue
            for row in excluded:
                evidence = {'index_code': code, 'source_url': item.get('url', INDEX_SOURCES[code]['url']),
                    'content_sha256': item.get('content_sha256'), 'fetched_at': item.get('fetched_at'), **deepcopy(row)}
                pool_exclusions.append(evidence)
                exclusions[(code, row['symbol'])] = evidence
            for row in rows:
                symbol = row['symbol']
                symbols.add(symbol)
                company = companies.setdefault(symbol, {'symbol': symbol, 'name': row['name'],
                    'admission_status': 'pending_verification', 'pool_ids': [], 'source_indexes': [],
                    'source_labels': [], 'source_records': []})
                if pool not in company['pool_ids']:
                    company['pool_ids'].append(pool)
                if code not in company['source_indexes']:
                    company['source_indexes'].append(code)
                    company['source_labels'].append(INDEX_SOURCES[code]['name'])
                    company['source_records'].append({'index_code': code, 'snapshot_date': snapshot,
                        'effective_date': None, 'fetched_at': item.get('fetched_at'),
                        'source_url': item.get('url', INDEX_SOURCES[code]['url']),
                        'content_sha256': item.get('content_sha256'), **deepcopy(row)})
        output[pool] = {'index_codes': list(codes), 'status': 'complete' if complete else 'partial',
                        'complete': complete, 'companies': sorted(symbols), 'scope_exclusions': pool_exclusions}
    for company in companies.values():
        company['sources'] = list(company['source_indexes'])
        company['research_hints'] = deepcopy(company['source_records'])
    complete = all(pool['complete'] for pool in output.values())
    return {'as_of': cutoff, 'items': records, 'pools': output, 'status': 'complete' if complete else 'partial',
        'scope_complete': complete, 'scope_exclusions': list(exclusions.values()),
        'companies': [companies[symbol] for symbol in sorted(companies)], 'research_only': True,
        'financial_verified': False, 'formal_screening_enabled': False, 'members_published': 0,
        'index_removal_triggers_exit': False, 'requires_review': True}

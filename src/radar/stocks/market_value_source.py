"""从已保存的东方财富原始响应计算供应商总市值研究结果。"""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

from radar.stocks.admission import _amount, _day
from radar.stocks.index_scope import POOL_INDEX_CODES
from radar.stocks.structured_collect import _json, _safe_path

_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
_FLOOR = Decimal(20000000000)
_CODES = ('capitalization_current', 'capitalization_average_60d')


def _read(path: Path, digest: str, size: int | None = None) -> Any:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError('来源路径须为绝对路径')
    _safe_path(path)
    if not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
        raise ValueError('来源摘要无效')
    if size is not None and (type(size) is not int or size < 0 or size > 8 * 1024 * 1024):
        raise ValueError('响应长度无效')
    with path.open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024 or (size is not None and len(raw) != size) or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('来源长度或摘要不符')
    return _json(raw)


def _scope(value: Any, symbol: str, cutoff: date) -> None:
    if not isinstance(value, dict):
        raise TypeError('冻结范围无效')
    scope_day = _day(value.get('as_of'))
    if (value.get('version') != 'official-index-scope-v1' or value.get('status') != 'complete'
            or value.get('scope_complete') is not True or scope_day is None or scope_day > cutoff):
        raise ValueError('冻结范围状态或日期无效')
    companies = value.get('companies')
    pools = value.get('pools')
    if not isinstance(companies, list) or not companies or not isinstance(pools, dict) or set(pools) != set(POOL_INDEX_CODES):
        raise ValueError('冻结范围公司或池无效')
    seen: set[str] = set()
    allowed = set().union(*map(set, POOL_INDEX_CODES.values()))
    for row in companies:
        if not isinstance(row, dict):
            raise TypeError('冻结范围公司无效')
        code, name, indexes, memberships = row.get('symbol'), row.get('name'), row.get('source_indexes'), row.get('pool_ids')
        if (not isinstance(code, str) or re.fullmatch(r'(?:60[0135]|688|000|001|002|003|30[012])\d{3}', code) is None
                or code in seen or not isinstance(name, str) or not name.strip() or not isinstance(indexes, list)
                or not indexes or any(not isinstance(x, str) or x not in allowed for x in indexes)
                or len(indexes) != len(set(indexes)) or not isinstance(memberships, list)):
            raise ValueError('冻结范围公司身份无效')
        expected = {pool for pool, indexes_for_pool in POOL_INDEX_CODES.items() if set(indexes).intersection(indexes_for_pool)}
        if len(memberships) != len(expected) or set(memberships) != expected:
            raise ValueError('冻结范围池归属无效')
        seen.add(code)
    if symbol not in seen:
        raise ValueError('公司不在冻结范围')
    for pool, indexes in POOL_INDEX_CODES.items():
        row = pools[pool]
        expected = {c['symbol'] for c in companies if pool in c['pool_ids']}
        if (not isinstance(row, dict) or row.get('status') != 'complete' or row.get('complete') is not True
                or row.get('index_codes') != list(indexes) or not isinstance(row.get('companies'), list)
                or len(row['companies']) != len(expected) or set(row['companies']) != expected):
            raise ValueError('冻结池范围不完整')


def _window(value: Any, cutoff: date) -> list[date]:
    if (not isinstance(value, dict) or value.get('version') != 'stock-market-window-v1' or value.get('market') != 'XSHG'
            or value.get('as_of') != cutoff.isoformat() or value.get('research_only') is not True
            or value.get('formal_screening_enabled') is not False):
        raise ValueError('市场窗口元数据无效')
    days = value.get('days')
    if not isinstance(days, list) or len(days) != 60:
        raise ValueError('市场窗口不是60日')
    parsed = [_day(x) for x in days]
    if any(d is None for d in parsed):
        raise ValueError('市场窗口日期无效')
    valid = [d for d in parsed if d is not None]
    if valid != sorted(set(valid)) or valid[-1] > cutoff or value.get('latest_completed') != valid[-1].isoformat():
        raise ValueError('市场窗口顺序或截止日无效')
    calendar = value.get('calendar')
    if not isinstance(calendar, dict):
        raise TypeError('市场日历无效')
    coverage_start, coverage_end = _day(calendar.get('coverage_start')), _day(calendar.get('coverage_end'))
    if (not isinstance(calendar.get('source'), str) or not calendar['source'].strip()
            or not isinstance(calendar.get('snapshot_sha256'), str)
            or re.fullmatch(r'[0-9a-f]{64}', calendar['snapshot_sha256']) is None
            or coverage_start is None or coverage_end is None or coverage_start > valid[0] or coverage_end < cutoff):
        raise ValueError('市场日历覆盖或来源无效')
    fetched = datetime.fromisoformat(calendar.get('fetched_at', ''))
    if fetched.tzinfo is None or fetched.utcoffset() is None:
        raise ValueError('市场日历抓取时间缺少时区')
    return valid


def evaluate_supplier_market_value(*, symbol: str, as_of: date, scope_path: Path, scope_sha256: str,
                                   window_path: Path, window_sha256: str, artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """验证来源后计算两项规模指标；输入损坏保持待核验，不写业务数据。"""
    result: dict[str, Any] = {'symbol': symbol, 'as_of': as_of.isoformat() if type(as_of) is date else None,
                              'status': 'unavailable', 'checks': [], 'source': {'basis': 'eastmoney_supplier_total_market_cap',
                              'scope_sha256': scope_sha256, 'window_sha256': window_sha256, 'artifacts': []}}
    try:
        if type(as_of) is not date:
            raise ValueError('评估日期无效')
        _scope(_read(scope_path, scope_sha256), symbol, as_of)
        days = _window(_read(window_path, window_sha256), as_of)
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError('缺少供应商响应')
        wanted = set(days)
        by_day: dict[date, Decimal] = {}
        identities: set[str] = set()
        for artifact in artifacts:
            if not isinstance(artifact, dict) or artifact.get('source_url') != _URL:
                raise ValueError('供应商来源无效')
            if 'fetched_at' not in artifact:
                raise ValueError('抓取时间缺失且未声明时间依据')
            fetched_text = artifact['fetched_at']
            time_basis = artifact.get('time_basis')
            fetched = None
            if fetched_text is None:
                if time_basis != 'not_recorded':
                    raise ValueError('抓取时间未知须明确标注未记录')
            else:
                if not isinstance(fetched_text, str) or time_basis not in (None, 'recorded'):
                    raise ValueError('抓取时间与记录状态不一致')
                fetched = datetime.fromisoformat(fetched_text)
                if fetched.tzinfo is None or fetched.utcoffset() is None:
                    raise ValueError('抓取时间缺少时区')
                if fetched > datetime.now(UTC):
                    raise ValueError('抓取时间不能位于未来')
                time_basis = 'recorded'
            digest = artifact.get('sha256')
            if not isinstance(digest, str) or digest in identities:
                raise ValueError('重复或缺失响应摘要')
            identities.add(digest)
            path, size = artifact.get('path'), artifact.get('bytes')
            if not isinstance(path, Path) or type(size) is not int:
                raise TypeError('响应路径或长度缺失')
            payload = _read(path, digest, size)
            body = payload.get('result') if isinstance(payload, dict) else None
            if (not isinstance(payload, dict) or payload.get('success') is not True or not isinstance(body, dict)
                    or body.get('pages') != 1 or not isinstance(body.get('data'), list)
                    or type(body.get('count')) is not int or body['count'] != len(body['data'])):
                raise ValueError('响应分页或状态无效')
            for row in body['data']:
                if not isinstance(row, dict) or row.get('SECURITY_CODE') != symbol:
                    raise ValueError('响应公司身份不符')
                raw_day = row.get('TRADE_DATE')
                if not isinstance(raw_day, str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}(?: 00:00:00)?', raw_day) is None:
                    raise ValueError('响应日期无效')
                day = _day(raw_day[:10])
                if day is None or (fetched is not None and day > fetched.date()):
                    raise ValueError('响应日期晚于抓取时间或无效')
                if day not in wanted:
                    continue
                if day in by_day:
                    raise ValueError('响应日期重复或冲突')
                amount = _amount(row.get('TOTAL_MARKET_CAP'))
                if amount is None or amount < 0:
                    raise ValueError('供应商总市值无效')
                by_day[day] = amount
            result['source']['artifacts'].append({'sha256': digest, 'bytes': artifact['bytes'], 'fetched_at': fetched_text,
                                                  'time_basis': time_basis, 'source_url': _URL})
        current = by_day.get(days[-1])
        if current is None:
            current_check = {'code': _CODES[0], 'outcome': 'pending_verification', 'reason': '最新交易日市值缺失'}
        else:
            current_check = {'code': _CODES[0], 'outcome': 'passed' if current >= _FLOOR else 'failed',
                             'reason': '供应商总市值须不低于200亿元', 'values': {'amount_yuan': str(current), 'date': days[-1].isoformat()}}
        if set(by_day) != wanted:
            average_check = {'code': _CODES[1], 'outcome': 'pending_verification', 'reason': '60个交易日市值未逐日完整覆盖'}
        else:
            with localcontext() as context:
                context.prec = 1100
                total = sum(by_day.values(), Decimal(0))
                avg = (total / Decimal(60)).quantize(Decimal('0.01'))
                outcome = 'passed' if total >= _FLOOR * 60 else 'failed'
            average_check = {'code': _CODES[1], 'outcome': outcome, 'reason': '供应商总市值60日均值须不低于200亿元',
                             'values': {'average_amount_yuan': str(avg), 'observations': 60}}
        result.update(status='current', checks=[current_check, average_check])
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        result['checks'] = [{'code': code, 'outcome': 'pending_verification', 'reason': '供应商资料缺失或校验失败'} for code in _CODES]
    return result

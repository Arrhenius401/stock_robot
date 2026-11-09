"""读取交易所年度信息披露评价，作为治理正向依据；C/D 或未覆盖不能通过。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

EVALUATION_PATH = Path(__file__).resolve().parents[3] / 'data' / 'exchange_disclosure_evaluation.json'
_VERSION = 'exchange-disclosure-evaluation-v1'
_GRADES = {'A', 'B', 'C', 'D'}
_HOSTS = {'www.sse.com.cn', 'www.szse.cn', 'docs.static.szse.cn'}


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _official(value: Any) -> bool:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (parsed.scheme in {'https', 'http'} and (parsed.hostname or '') in _HOSTS
            and parsed.username is None and parsed.password is None and parsed.port in (None, 443, 80))


def _valid_sources(payload: dict[str, Any], cutoff: date) -> bool:
    sources = payload.get('sources')
    if not isinstance(sources, list) or not sources:
        return False
    for source in sources:
        if not isinstance(source, dict) or source.get('exchange') not in {'SSE', 'SZSE'}:
            return False
        if not isinstance(source.get('title'), str) or not source['title'].strip():
            return False
        if not _official(source.get('notice_url')) or not _official(source.get('pdf_url')):
            return False
        published = _day(source.get('published_on'))
        if published is None or published > cutoff:
            return False
        if not isinstance(source.get('sha256'), str) or re.fullmatch('[0-9a-f]{64}', source['sha256']) is None:
            return False
        if type(source.get('bytes')) is not int or source['bytes'] <= 0:
            return False
    return True


def load_disclosure_evaluation(symbol: str, as_of: date, *, path: Path | None = None) -> dict[str, Any]:
    """只读取已发布、覆盖截点的评价；缺失等级仍为 current 但不授予通过。"""
    result: dict[str, Any] = {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'unavailable',
                              'grade': None, 'reason': '交易所信息披露评价缺失或校验失败'}
    target = EVALUATION_PATH if path is None else path
    try:
        raw = target.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get('version') != _VERSION:
            raise ValueError('评价版本无效')
        period_start, period_end = _day(payload.get('period_start')), _day(payload.get('period_end'))
        published = _day(payload.get('published_on'))
        if period_start is None or period_end is None or published is None or not period_start <= period_end <= published:
            raise ValueError('评价期间或发布日期无效')
        if published > as_of:
            raise ValueError('评价尚未发布')
        if not _valid_sources(payload, as_of):
            raise ValueError('评价来源或日期无效')
        grades = payload.get('grades')
        if not isinstance(grades, dict) or len(grades) < 1000:
            raise ValueError('评价覆盖不足')
        for code, grade in grades.items():
            if not isinstance(code, str) or re.fullmatch(r'\d{6}', code) is None or grade not in _GRADES:
                raise ValueError('评价代码或等级无效')
        grade = grades.get(symbol)
        result.update(status='current', grade=grade, period_start=period_start.isoformat(),
                      period_end=period_end.isoformat(), published_on=published.isoformat(),
                      file_sha256=hashlib.sha256(raw).hexdigest(), sources=payload['sources'],
                      reason=('交易所信息披露评价等级已绑定' if grade else '本期交易所信息披露评价未覆盖该公司'))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    return result

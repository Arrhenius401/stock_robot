"""读取严格第三方/协会引用的行业地位披露，并复读原始 F10 响应。"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

_REQUIRED = ('symbol', 'scope', 'metric', 'value', 'source_name', 'quote', 'report_date', 'source_url',
             'raw_file', 'raw_sha256', 'disclosed_on', 'announcement_title', 'announcement_url',
             'disclosure_file', 'disclosure_sha256')
_NUMBERS = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}


def _norm(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    return re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', value))


def _rank_in_quote(quote: str) -> int | None:
    text = _norm(quote)
    if '前三' in text:
        return 3
    match = re.search(r'第([一二三四五六七八九十\d]+)(?=(?:名|位|强|[，,。；;、（）()]|$))', text)
    if match is None:
        match = re.search(r'([一二三四五六七八九十\d]+)(?:名|位)', text)
    if match is None:
        return None
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    return _NUMBERS.get(raw)


def _official_url(value: str, symbol: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme != 'https' or parsed.hostname != 'emweb.securities.eastmoney.com':
        return False
    if parsed.path != '/PC_HSF10/BusinessAnalysis/PageAjax':
        return False
    code = ('SH' if symbol.startswith('6') else 'SZ') + symbol
    return parse_qs(parsed.query).get('code') == [code]


def _raw_reviews(state_dir: Path, claim: dict[str, Any]) -> tuple[str, set[str]]:
    raw_file = claim.get('raw_file')
    expected = f"raw/{claim.get('symbol')}.json"
    if not isinstance(raw_file, str) or raw_file != expected:
        raise ValueError('行业地位原始文件路径不符')
    path = state_dir / 'stock-leadership' / raw_file
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != claim.get('raw_sha256'):
        raise ValueError('行业地位原始文件摘要不符')
    payload = json.loads(raw)
    rows = payload.get('jyps') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise TypeError('行业地位原始披露结构不符')
    text = ' '.join(str(row.get('BUSINESS_REVIEW') or '') for row in rows if isinstance(row, dict))
    dates = {str(row.get('REPORT_DATE') or '')[:10] for row in rows if isinstance(row, dict)}
    return text, dates


def _valid_disclosure(state_dir: Path, claim: dict[str, Any]) -> bool:
    disclosure_file = claim.get('disclosure_file')
    expected = f"disclosures/{claim.get('symbol')}.json"
    if not isinstance(disclosure_file, str) or disclosure_file != expected:
        return False
    try:
        raw = (state_dir / 'stock-leadership' / disclosure_file).read_bytes()
        if hashlib.sha256(raw).hexdigest() != claim.get('disclosure_sha256'):
            return False
        item = json.loads(raw)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if not isinstance(item, dict):
        return False
    if (item.get('title') != claim.get('announcement_title') or item.get('notice_date') != claim.get('disclosed_on')
            or not isinstance(item.get('url'), str) or not item['url'].startswith('https://data.eastmoney.com/notices/detail/')):
        return False
    return item['url'] == claim.get('announcement_url')


def _valid_claim(state_dir: Path, claim: Any, symbol: str, cutoff: date) -> bool:
    if not isinstance(claim, dict) or any(not isinstance(claim.get(key), str) or not claim[key].strip() for key in _REQUIRED):
        return False
    if claim.get('symbol') != symbol or claim.get('metric') not in {'share', 'rank'}:
        return False
    rank = claim.get('rank')
    if type(rank) is not int or rank < 1 or rank > 3:
        return False
    try:
        report = date.fromisoformat(claim['report_date'])
        disclosed = date.fromisoformat(claim['disclosed_on'])
    except ValueError:
        return False
    if report > disclosed or disclosed > cutoff:
        return False
    if not _official_url(claim['source_url'], symbol):
        return False
    try:
        text, dates = _raw_reviews(state_dir, claim)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    quote = claim['quote']
    if quote not in text or claim['report_date'] not in dates:
        return False
    if _norm(claim['source_name']) not in _norm(text) or _norm(claim['scope']) not in _norm(quote):
        return False
    parsed_rank = _rank_in_quote(quote)
    if parsed_rank != rank:
        return False
    value = _norm(claim['value'])
    if claim.get('metric') == 'share' and value.endswith('%'):
        if value not in _norm(quote):
            return False
    elif value not in _norm(quote):
        return False
    return _valid_disclosure(state_dir, claim)


def load_leadership_source(state_dir: Path, symbol: str, as_of: date) -> dict[str, Any]:
    out: dict[str, Any] = {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'unavailable',
                           'observation': 'unknown', 'claims': [], 'reason': '行业地位来源缺失'}
    root = state_dir / 'stock-leadership'
    path = root / 'leadership.json'
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get('as_of') != as_of.isoformat() or not isinstance(payload.get('symbols'), dict):
            raise ValueError('行业地位来源截点或结构不符')
        claims = payload['symbols'].get(symbol, [])
        if not isinstance(claims, list) or not all(_valid_claim(state_dir, item, symbol, as_of) for item in claims):
            raise ValueError('行业地位声明无效')
        out.update(status='current', observation='observed' if claims else 'not_observed', claims=claims,
                   file_sha256=hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return out

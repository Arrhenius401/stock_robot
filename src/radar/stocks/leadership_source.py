"""读取严格第三方/协会引用的行业地位披露，并复读原始 F10 响应。"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

_REQUIRED = ('symbol', 'scope', 'metric', 'value', 'source_name', 'quote', 'report_date', 'source_url',
             'raw_file', 'raw_sha256')


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
    except ValueError:
        return False
    if report > cutoff or (cutoff - report).days > 366:
        return False
    if not _official_url(claim['source_url'], symbol):
        return False
    try:
        text, dates = _raw_reviews(state_dir, claim)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    return claim['quote'] in text and claim['report_date'] in dates


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

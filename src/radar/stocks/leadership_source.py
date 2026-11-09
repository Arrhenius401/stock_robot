"""读取严格第三方/协会引用的行业地位披露。"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_REQUIRED = ('symbol', 'scope', 'metric', 'value', 'source_name', 'quote', 'report_date', 'source_url')


def _valid_claim(claim: Any, symbol: str, cutoff: date) -> bool:
    if not isinstance(claim, dict) or any(not isinstance(claim.get(key), str) or not claim[key].strip() for key in _REQUIRED):
        return False
    if claim.get('symbol') != symbol or claim.get('metric') not in {'share', 'rank'}:
        return False
    try:
        report = date.fromisoformat(claim['report_date'])
    except ValueError:
        return False
    if report > cutoff or (cutoff - report).days > 366:
        return False
    try:
        parsed = urlsplit(claim['source_url'])
    except ValueError:
        return False
    return parsed.scheme == 'https' and bool(parsed.hostname) and bool(parsed.path)


def load_leadership_source(state_dir: Path, symbol: str, as_of: date) -> dict[str, Any]:
    out: dict[str, Any] = {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'unavailable',
                           'observation': 'unknown', 'claims': [], 'reason': '行业地位来源缺失'}
    path = state_dir / 'stock-leadership' / 'leadership.json'
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get('as_of') != as_of.isoformat() or not isinstance(payload.get('symbols'), dict):
            raise ValueError('行业地位来源截点或结构不符')
        claims = payload['symbols'].get(symbol, [])
        if not isinstance(claims, list) or not all(_valid_claim(item, symbol, as_of) for item in claims):
            raise ValueError('行业地位声明无效')
        out.update(status='current', observation='observed' if claims else 'not_observed', claims=claims,
                   file_sha256=hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return out

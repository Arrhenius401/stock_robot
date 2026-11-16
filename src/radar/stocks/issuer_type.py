"""按官方持牌法人名录和主营业务边界保守判定发行人类型。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROSTER_PATH = Path(__file__).resolve().parents[3] / 'data' / 'financial_issuer_rosters.json'
_REQUIRED_SOURCES = ('banking', 'insurance', 'financial_holding', 'securities')
_OFFICIAL_HOSTS = ('nfra.gov.cn', 'csrc.gov.cn')
_FINANCIAL_TERMS = ('银行', '证券', '保险', '信托', '租赁', '期货', '基金', '资产管理', '财险', '寿险',
                    '金融控股', '金融租赁', '消费金融', '汽车金融', '金融资产')


def _norm(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    return re.sub(r'\s+', '', value).replace('（', '(').replace('）', ')')


def _valid_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or not host or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or not parsed.path):
            return False
    except ValueError:
        return False
    return any(host == domain or host.endswith('.' + domain) for domain in _OFFICIAL_HOSTS)


def _source_day(value: Any, *, month: bool = False) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        if month and re.fullmatch(r'\d{4}-\d{2}', value):
            return date(int(value[:4]), int(value[5:7]), 1)
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value or month else None


def _valid_sources(payload: Any, as_of: date | None = None) -> bool:
    if not isinstance(payload, dict):
        return False
    roster_day = _source_day(payload.get('as_of'))
    if roster_day is None or (as_of is not None and roster_day > as_of):
        return False
    sources = payload.get('sources')
    if not isinstance(sources, dict) or any(key not in sources for key in _REQUIRED_SOURCES):
        return False
    for key in _REQUIRED_SOURCES:
        source = sources[key]
        if not isinstance(source, dict):
            return False
        if not isinstance(source.get('title'), str) or not source['title'].strip():
            return False
        source_day = _source_day(source.get('as_of'), month=key == 'securities')
        if source_day is None or (as_of is not None and source_day > as_of):
            return False
        if not _valid_url(source.get('url')):
            return False
    names = payload.get('names')
    if not isinstance(names, dict):
        return False
    for key in ('bank', 'insurance', 'securities', 'other_financial'):
        rows = names.get(key)
        if not isinstance(rows, list) or not rows or any(not isinstance(item, str) or not item.strip() for item in rows):
            return False
    return True


def _load_rosters(as_of: date | None = None) -> tuple[dict[str, Any], str] | None:
    """每次重新读取名录；来源、日期、结构或文件损坏时不得沿用旧缓存。"""
    try:
        raw = ROSTER_PATH.read_bytes()
        payload = json.loads(raw)
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return None
    if not _valid_sources(payload, as_of):
        return None
    return payload, hashlib.sha256(raw).hexdigest()


def _source(rosters: dict[str, Any], digest: str) -> dict[str, Any]:
    raw_sources: Any = rosters.get('sources')
    sources: dict[str, Any] = raw_sources if isinstance(raw_sources, dict) else {}
    return {**sources, 'as_of': rosters.get('as_of'), 'file_sha256': digest}


def classify_issuer(full_name: Any, industry_label: Any, as_of: date | None = None) -> dict[str, Any]:
    """正例精确匹配；未命中且非金融行业/主营才判普通，否则保留未分类。"""
    loaded = _load_rosters(as_of)
    if loaded is None:
        return {'issuer_type': None, 'status': 'unavailable',
                'reason': '发行人类型名录缺失、损坏、来源晚于研究截点或元数据不完整'}
    payload, digest = loaded
    source = _source(payload, digest)
    name = _norm(full_name)
    if not name:
        return {'issuer_type': None, 'status': 'pending_verification',
                'reason': '缺少上市主体全称', 'source': source}
    industry = _norm(industry_label)
    rosters = payload['names']
    for issuer_type in ('bank', 'insurance', 'securities', 'other_financial'):
        if name in {_norm(item) for item in rosters.get(issuer_type, [])}:
            return {'issuer_type': issuer_type, 'status': 'verified',
                    'reason': f'官方持牌法人名录全称匹配：{issuer_type}', 'source': source}
    if not industry:
        return {'issuer_type': None, 'status': 'pending_verification',
                'reason': '行业字段缺失，不能判普通企业', 'source': source}
    if '金融' in industry or any(term in name for term in _FINANCIAL_TERMS):
        return {'issuer_type': None, 'status': 'unclassified_financial',
                'reason': '未命中持牌名录但行业或名称含金融业务，不能判普通企业', 'source': source}
    return {'issuer_type': 'ordinary', 'status': 'verified',
            'reason': '未命中金融持牌名录且行业与名称均非金融业务', 'source': source}

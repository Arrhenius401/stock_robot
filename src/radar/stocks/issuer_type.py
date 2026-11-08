"""按官方持牌法人名录和主营业务边界保守判定发行人类型。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROSTER_PATH = Path(__file__).resolve().parents[3] / 'data' / 'financial_issuer_rosters.json'
_FINANCIAL_TERMS = ('银行', '证券', '保险', '信托', '租赁', '期货', '基金', '资产管理', '财险', '寿险',
                    '金融控股', '金融租赁', '消费金融', '汽车金融', '金融资产')


def _norm(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    return re.sub(r'\s+', '', value).replace('（', '(').replace('）', ')')


def _load_rosters() -> tuple[dict[str, Any], str] | None:
    """每次重新读取名录，副本损坏或结构不完整时不得沿用旧缓存。"""
    try:
        raw = ROSTER_PATH.read_bytes()
        payload = json.loads(raw)
    except (OSError, ValueError, TypeError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get('names'), dict):
        return None
    names = payload['names']
    for key in ('bank', 'insurance', 'securities', 'other_financial'):
        if not isinstance(names.get(key), list):
            return None
    return payload, hashlib.sha256(raw).hexdigest()


def _source(rosters: dict[str, Any], digest: str) -> dict[str, Any]:
    raw_sources: Any = rosters.get('sources')
    sources: dict[str, Any] = raw_sources if isinstance(raw_sources, dict) else {}
    return {**sources, 'as_of': rosters.get('as_of'), 'file_sha256': digest}


def classify_issuer(full_name: Any, industry_label: Any) -> dict[str, Any]:
    """正例精确匹配；未命中且非金融行业/主营才判普通，否则保留未分类。"""
    loaded = _load_rosters()
    if loaded is None:
        return {'issuer_type': None, 'status': 'unavailable',
                'reason': '发行人类型名录缺失、损坏或结构不完整'}
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

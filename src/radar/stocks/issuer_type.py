"""按官方持牌法人名录和主营业务边界保守判定发行人类型。"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

ROSTER_PATH = Path(__file__).resolve().parents[3] / 'data' / 'financial_issuer_rosters.json'
_ISSUER_TYPES = ('ordinary', 'bank', 'securities', 'insurance', 'other_financial')
_FINANCIAL_TERMS = ('银行', '证券', '保险', '信托', '租赁', '期货', '基金', '资产管理', '财险', '寿险',
                    '金融控股', '金融租赁', '消费金融', '汽车金融', '金融资产')


def _norm(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    return re.sub(r'\s+', '', value).replace('（', '(').replace('）', ')')


@lru_cache(maxsize=1)
def _rosters() -> dict[str, Any]:
    payload = json.loads(ROSTER_PATH.read_text(encoding='utf-8'))
    if not isinstance(payload, dict) or not isinstance(payload.get('names'), dict):
        raise TypeError('发行人类型名录无效')
    return payload


def classify_issuer(full_name: Any, industry_label: Any) -> dict[str, Any]:
    """正例精确匹配；未命中且非金融行业/主营才判普通，否则保留未分类。"""
    name, industry = _norm(full_name), _norm(industry_label)
    if not name:
        return {'issuer_type': None, 'status': 'pending_verification', 'reason': '缺少上市主体全称'}
    rosters = _rosters()['names']
    for issuer_type in ('bank', 'insurance', 'securities', 'other_financial'):
        if name in {_norm(item) for item in rosters.get(issuer_type, [])}:
            return {'issuer_type': issuer_type, 'status': 'verified',
                    'reason': f'官方持牌法人名录全称匹配：{issuer_type}', 'source': _rosters().get('sources')}
    if '金融' in industry or any(term in name for term in _FINANCIAL_TERMS):
        return {'issuer_type': None, 'status': 'unclassified_financial',
                'reason': '未命中持牌名录但行业或名称含金融业务，不能判普通企业'}
    return {'issuer_type': 'ordinary', 'status': 'verified',
            'reason': '未命中金融持牌名录且行业与名称均非金融业务', 'source': _rosters().get('sources')}

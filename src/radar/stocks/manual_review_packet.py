"""按需投影单家公司人工审核依据，不产生审核结论。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from radar.stocks.research_store import _symbol

_COMMON = ('security', 'audit', 'governance', 'leadership', 'financial_quality')
_KINDS = {*(f'admission_{name}' for name in _COMMON), 'style_cash_review'}
_SOURCE_FIELDS = ('symbol', 'status', 'as_of', 'evaluation_as_of', 'reason', 'channel', 'run_id',
                  'scope_as_of', 'scope_sha256', 'manifest_sha256', 'snapshot_sha256',
                  'task_identity_sha256', 'item_sha256', 'fetched_at', 'source', 'industry', 'name_match', 'roster_name',
                  'checks', 'reference_task')
_EVIDENCE_FIELDS = ('evidence_id', 'symbol', 'kind', 'review_status', 'confirmable', 'source_url',
                    'content_sha256', 'disclosed_on', 'latest_review')
_PAYLOAD_FIELDS = ('verdict', 'basis', 'years', 'exchange', 'security_type', 'a_listed_on', 'issuer_type',
                   'industry_level1', 'industry_level2', 'business_scope')


def build_manual_review_packet(detail: Any) -> dict[str, Any]:
    """只使用详情已验证结果；不调用网络、模型或写入业务数据。"""
    if not isinstance(detail, dict) or not isinstance(detail.get('company'), dict):
        raise TypeError('公司详情无效')
    company = detail['company']
    symbol = _symbol(company.get('symbol'))
    report = detail.get('evaluation')
    if not isinstance(report, dict):
        raise TypeError('公司评估无效')
    items = report.get('items')
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise ValueError('审核包须绑定单家公司评估')
    item = items[0]
    if item.get('symbol') != symbol or item.get('evaluation_valid') is not True:
        raise ValueError('公司评估身份不符或已失效')
    common = item.get('checks')
    pools = item.get('pools')
    red = pools.get('dividend_value') if isinstance(pools, dict) else None
    style = red.get('checks') if isinstance(red, dict) else None
    if not isinstance(common, list) or not isinstance(style, list):
        raise TypeError('公司审核条件不完整')
    checks = []
    for code in (*_COMMON, 'cash_review'):
        matches = [row for row in (style if code == 'cash_review' else common)
                   if isinstance(row, dict) and row.get('code') == code]
        if len(matches) != 1:
            raise ValueError('审核条件缺失或重复')
        checks.append(deepcopy(matches[0]))
    evidence = []
    for row in detail.get('evidence', []):
        if not isinstance(row, dict) or row.get('kind') not in _KINDS:
            continue
        value = {key: deepcopy(row[key]) for key in _EVIDENCE_FIELDS if key in row}
        payload = row.get('payload')
        value['payload'] = ({key: deepcopy(payload[key]) for key in _PAYLOAD_FIELDS if key in payload}
                            if isinstance(payload, dict) else None)
        evidence.append(value)
    sources = {}
    for label, key in (('profile', 'company_profile_source'), ('financial', 'financial_source'), ('industry', 'industry_source')):
        source = item.get(key)
        if not isinstance(source, dict):
            sources[label] = {'status': 'unavailable'}
            continue
        value = {name: deepcopy(source[name]) for name in _SOURCE_FIELDS if name in source}
        financial = source.get('financial')
        if label == 'financial' and isinstance(financial, dict):
            value['financial'] = {name: deepcopy(financial[name]) for name in ('cash_flow_reference', 'issuer_applicability', 'source_company_type') if name in financial}
        sources[label] = value
    audit_clues: dict[str, Any] = {'symbol': symbol, 'as_of': report.get('as_of'), 'status': 'unavailable',
                                   'annual': [], 'reviewed': False, 'formal_screening_enabled': False}
    candidate = detail.get('audit_clues')
    if (isinstance(candidate, dict) and candidate.get('symbol') == symbol
            and candidate.get('as_of') == report.get('as_of')
            and candidate.get('status') in ('available', 'historical')
            and candidate.get('reviewed') is False and candidate.get('formal_screening_enabled') is False
            and isinstance(candidate.get('annual'), list)):
        audit_clues = deepcopy(candidate)
    return {'symbol': symbol, 'name': company.get('name'), 'source_revision': company.get('revision'),
            'as_of': report.get('as_of'), 'checks': checks, 'evidence': evidence, 'sources': sources,
            'audit_clues': audit_clues,
            'research_only': True, 'formal_screening_enabled': False, 'members_published': 0}

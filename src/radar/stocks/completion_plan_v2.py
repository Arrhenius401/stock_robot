"""补全计划 v2：在风格缺项之外显式表达共同条件缺项与共享证据阶段。

只读生成；失败条件永不进入计划，缺失不等于不满足。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from radar.stocks.completion_adapters import ADAPTERS
from radar.stocks.completion_plan import build_completion_plan

PLAN_VERSION = 'completion-plan-v2'
PENDING = {'pending_verification', 'unavailable'}
SHARED_CONDITIONS = {'governance': 'governance', 'leadership': 'industry_scale'}
_FIRM_FIXABLE = {'listing_age', 'capitalization_current', 'capitalization_average_60d',
                 'annual_parent_profit', 'annual_deducted_profit', 'ttm_parent_profit',
                 'ttm_deducted_profit', 'audit', 'financial_quality'}


def _sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _common_pending(details: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按公司汇总共同条件待检验项，保留名称与所属池。"""
    result: dict[str, dict[str, Any]] = {}
    for row in details:
        if row.get('分类') != 'common' or row.get('结果') not in PENDING:
            continue
        symbol = row.get('代码')
        condition = row.get('条件')
        if not isinstance(symbol, str) or not isinstance(condition, str):
            continue
        item = result.setdefault(symbol, {'name': row.get('名称') or symbol, 'conditions': {}, 'pools': set()})
        item['conditions'].setdefault(condition, str(row.get('原因') or ''))
        if isinstance(row.get('池'), str):
            item['pools'].add(row['池'])
    return result


def build_completion_plan_v2(details: list[dict[str, Any]], db: Path, as_of: date, *,
                             pools: set[str] | None = None, symbols: set[str] | None = None,
                             shared: dict[str, Any] | None = None) -> dict[str, Any]:
    """生成 v2 计划：共同条件与风格缺项分类，共享阶段去重。"""
    shared = shared or {}
    base = build_completion_plan(details, db, as_of, pools=pools, symbols=symbols)
    items: list[dict[str, Any]] = []
    for item in base['items']:
        source = item.get('source') if isinstance(item.get('source'), dict) else {}
        kind = source.get('kind')
        if kind in ADAPTERS:
            evidence_class, executable = 'auto_firm_refresh', {
                'mode': 'firm', 'provider': ADAPTERS[kind].provider_key}
        elif kind == 'annual_report_profit_distribution':
            evidence_class, executable = 'external_required', {'mode': 'none'}
        else:
            evidence_class, executable = 'external_required', {'mode': 'none'}
        items.append({**item, 'group': 'style', 'evidence_class': evidence_class, 'executable': executable})
    common = _common_pending(details)
    stage_symbols: dict[str, list[str]] = {'governance': [], 'industry_scale': []}
    for symbol, entry in sorted(common.items()):
        if pools and not (entry['pools'] & pools):
            continue
        if symbols and symbol not in symbols:
            continue
        for condition, reason in sorted(entry['conditions'].items()):
            stage = SHARED_CONDITIONS.get(condition)
            ready = bool((shared.get(stage) or {}).get('current')) if stage else False
            if stage and not ready:
                evidence_class, executable = 'shared_refresh', {'mode': 'shared_stage', 'stage': stage}
                stage_symbols[stage].append(symbol)
            elif condition in _FIRM_FIXABLE:
                evidence_class, executable = 'auto_firm_refresh', {'mode': 'firm', 'provider': 'financial_review'}
            else:
                evidence_class, executable = 'external_required', {'mode': 'none'}
            items.append({'symbol': symbol, 'name': entry['name'], 'pools': sorted(entry['pools']),
                          'condition': condition, 'current': 'pending_verification', 'group': 'common',
                          'gap': {'reason': reason or '共同条件缺少可复核证据'},
                          'source': {'kind': 'shared_evidence' if executable['mode'] == 'shared_stage' else 'manual_review'},
                          'expected': 'pending_verification', 'proof_required': ['source_url', 'disclosed_on', 'content_sha256'],
                          'evidence_class': evidence_class, 'executable': executable})
    stages = []
    for stage, label, cost, reason in (
            ('governance', '交易所监管快照', '全市场 4 类监管列表，约 240 页', '治理条件需要与截点同日的监管快照'),
            ('industry_scale', '同业规模排名', '同业范围全部成员的同截点市值', '行业地位需要完整同业范围与同截点市值'),
    ):
        if not stage_symbols[stage]:
            continue
        stages.append({'stage': stage, 'label': label, 'reason': (shared.get(stage) or {}).get('reason') or reason,
                       'symbols': sorted(set(stage_symbols[stage])), 'symbol_count': len(set(stage_symbols[stage])),
                       'cost_hint': cost, 'cost': (shared.get(stage) or {}).get('cost')})
    blocked = [{'symbol': symbol, 'name': entry['name'], 'reason': '；'.join(sorted(entry['conditions'].values()))}
               for symbol, entry in sorted(common.items())
               if not any(item['symbol'] == symbol for item in items)]
    plan = {'version': PLAN_VERSION, 'as_of': as_of.isoformat(),
            'pools': sorted(pools) if pools else [], 'items': items, 'shared_stages': stages,
            'blocked_items': blocked,
            'summary': {'items': len(items), 'symbols': len({item['symbol'] for item in items}),
                        'by_condition': {condition: sum(1 for item in items if item['condition'] == condition)
                                         for condition in sorted({item['condition'] for item in items})},
                        'by_class': {label: sum(1 for item in items if item['evidence_class'] == label)
                                     for label in sorted({item['evidence_class'] for item in items})},
                        'shared_stages': len(stages)}}
    plan['plan_sha256'] = _sha(plan)
    return plan


def plan_automatic_symbols(plan: dict[str, Any]) -> set[str]:
    return {item['symbol'] for item in plan['items']
            if (item.get('executable') or {}).get('mode') in {'shared_stage', 'firm'}}


def plan_firm_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in plan['items'] if (item.get('executable') or {}).get('mode') == 'firm'
            and item.get('group') == 'style']


def plan_shared_stages(plan: dict[str, Any]) -> list[str]:
    return [stage['stage'] for stage in plan.get('shared_stages') or []]

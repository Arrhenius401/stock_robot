"""补全执行：读取计划、校验哈希、按 --yes 调用适配器，输出证据记录。

适配器只消费 completion_providers 的现有来源结果；本模块不再保留本地证据目录、
不再按 source.kind 写 if/elif 分派。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from radar.stocks.completion_adapters import ADAPTERS, AdapterResult

APPLY_VERSION = 'completion-apply-2026-11-11-v2'


def _sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def load_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict) or not isinstance(payload.get('items'), list):
        raise TypeError('补全计划格式无效')
    expected = payload.get('plan_sha256')
    body = {key: value for key, value in payload.items() if key not in {'plan_sha256', 'members_published'}}
    if expected != _sha(body):
        raise ValueError('补全计划哈希不匹配')
    return payload


def select_items(plan: dict[str, Any], select: set[str] | None) -> list[dict[str, Any]]:
    items = plan['items']
    if not select:
        return list(items)
    chosen = [item for item in items if item.get('symbol') in select or item.get('condition') in select]
    if not chosen:
        raise ValueError('没有匹配的计划项')
    return chosen


def apply_plan(plan: dict[str, Any], *, select: set[str] | None = None, dry_run: bool = False,
               yes: bool = False, providers: dict[str, Callable[..., Any]] | None = None) -> dict[str, Any]:
    """执行选中的计划项；默认 dry-run，只有 yes=True 才调用来源并产出 applied 结果。"""
    items = select_items(plan, select)
    run: dict[str, Any] = {'version': APPLY_VERSION, 'plan_sha256': plan.get('plan_sha256'),
                           'as_of': plan.get('as_of'), 'dry_run': dry_run or not yes, 'items': []}
    if dry_run or not yes:
        run['items'] = [{'symbol': item['symbol'], 'condition': item['condition'],
                         'source_kind': (item.get('source') or {}).get('kind'),
                         'status': 'planned'} for item in items]
        return run
    for item in items:
        raw_source = item.get('source') if isinstance(item, dict) else None
        raw_kind = raw_source.get('kind') if isinstance(raw_source, dict) else None
        source_kind = raw_kind if isinstance(raw_kind, str) else None
        adapter = ADAPTERS.get(source_kind) if source_kind else None
        if adapter is None:
            run['items'].append({'symbol': item.get('symbol'), 'condition': item.get('condition'),
                                 'status': 'pending_verification', 'reason': '没有对应适配器'})
            continue
        try:
            result: AdapterResult = adapter.collect(item, date.fromisoformat(plan['as_of']), providers)
        except Exception as exc:  # noqa: BLE001 数据源边界
            result = AdapterResult('pending_verification', reason=f'证据不可用：{type(exc).__name__}: {exc}')
        run['items'].append({'symbol': item.get('symbol'), 'condition': item.get('condition'),
                             'source_kind': source_kind, **result.as_dict()})
    return run

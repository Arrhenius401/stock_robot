"""股票专用采集结果投影；不改变采集预算、缓存和业务判定。"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from data.collection_state import CacheStatus, ValueResult, make_record
from data.schemas import ResultState

_SOURCES = {'financial': '东方财富结构化财务', 'index_scope': '官方指数成分',
            'market_history': '新浪股票日线', 'capital_history': '官方股本历史'}


def _time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.utcoffset() is not None and parsed <= datetime.now(UTC) else None
    except (ValueError, TypeError):
        return None


def collection_result(value: dict[str, Any] | ValueResult[dict[str, Any]], kind: str) -> ValueResult[dict[str, Any]]:
    """按原始证据投影状态；批次时间未知，逐组件保存真实原时间。"""
    if isinstance(value, ValueResult):
        return value
    data = deepcopy(value)
    components: list[dict[str, Any]] = []
    usable = False
    if kind == 'financial':
        sources = data.get('sources') or {}
        raw_records = data.get('records') or {}
        for key, meta in sources.items():
            if isinstance(meta, dict):
                components.append({**meta, 'component': key, 'usable': bool(raw_records.get(key))})
        for key, meta in (data.get('metadata') or {}).items():
            if isinstance(meta, dict):
                components.append({**meta, 'component': 'metadata.' + key, 'usable': True})
        for key in ('main', 'profit', 'cashflow'):
            if key not in sources:
                components.append({'component': key, 'usable': False})
        usable = any(bool(rows) for rows in raw_records.values())
    else:
        components = data.get('items') or []
        usable = bool(data.get('companies')) if kind == 'index_scope' else any(
            item.get('status') in {'downloaded', 'cached'} and bool(item.get('rows')) for item in components)
    records = []
    for item in components:
        success = item.get('usable') is True if kind == 'financial' else item.get('status') in {'downloaded', 'cached'}
        state: ResultState = 'complete' if success else 'failed'
        if success and kind == 'market_history' and item.get('window_complete') is False:
            state = 'partial'
        if success and kind == 'capital_history' and item.get('complete') is False:
            state = 'partial'
        if success and kind in {'market_history', 'capital_history'} and not item.get('rows'):
            complete_key = 'window_complete' if kind == 'market_history' else 'complete'
            state = 'empty' if item.get(complete_key) is True else 'failed'
        cache: CacheStatus = 'cache' if item.get('cache_status') == 'cache' or item.get('status') == 'cached' else 'unknown'
        if cache == 'unknown' and (item.get('cache_status') == 'live' or item.get('status') == 'downloaded'):
            cache = 'live'
        record = make_record(_SOURCES[kind], state, collected_at=_time(item.get('fetched_at')),
                             cache_status=cache, error_kind='' if state in {'complete', 'empty'} else ('invalid_data' if success else 'fetch_failed'),
                             error_message='' if state in {'complete', 'empty'} else '来源未取得通过完整性校验的数据，资料待补。')
        records.append({**record.model_dump(mode='json'), **{key: item[key] for key in ('component', 'symbol', 'index_code') if key in item}})
    state = 'complete' if usable and data.get('status') == 'complete' and all(r['result_state'] == 'complete' for r in records) else ('partial' if usable else 'failed')
    if records and not usable and all(r['result_state'] in {'complete', 'empty'} for r in records) and kind != 'financial':
        state = 'empty'
    # 旧测试桩/产物没有组件诊断时不补造来源时间；结果数据仍可按原校验使用。
    cache_values = {record['cache_status'] for record in records}
    cache_state: CacheStatus = 'cache' if cache_values == {'cache'} else ('live' if cache_values == {'live'} else 'unknown')
    record = make_record(_SOURCES[kind], state, cache_status=cache_state,
                         error_kind='' if state in {'complete', 'empty'} else 'fetch_failed',
                         error_message='' if state in {'complete', 'empty'} else '来源部分或全部资料待补，详见逐项结果。')
    data['collection_records'] = records
    return ValueResult(data, record)

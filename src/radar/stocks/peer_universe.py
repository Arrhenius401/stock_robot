"""同业范围：从已冻结的指数候选范围派生，独立于本次请求的评估范围。

排名只能在同一份完整范围内比较；这里保存范围文档、来源摘要和公司清单，
校验失败即不可用，绝不退回更小的范围。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any

from radar.stocks.index_scope import INDEX_SOURCES, POOL_INDEX_CODES
from radar.stocks.reference_task import reference_snapshot
from radar.stocks.research_store import _id
from radar.stocks.structured_collect import _json

VERSION = 'peer-universe-v1'
UNIVERSE = 'index_scope_union_v1'
_SYMBOL = re.compile(r'(?:60[0135]|688|000|001|002|003|30[012])\d{3}')
_SHA = re.compile(r'[0-9a-f]{64}')


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _sha(value: Any) -> str | None:
    return value if isinstance(value, str) and _SHA.fullmatch(value) else None


def read_scope_artifact(db: Path, run_id: str) -> tuple[dict[str, Any], str]:
    """只读读取指定 discover 任务的指数范围原文，返回文档与摘要。"""
    if not db.is_file():
        raise ValueError('来源状态缺少股票数据库')
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM stocks_update_runs WHERE run_id=? AND kind='discover_candidates'",
                                 (run_id,)).fetchone()
    if row is None:
        raise ValueError('指定任务不是候选范围发现任务')
    if row['status'] != 'completed':
        raise ValueError('候选范围发现任务未完成')
    item = next((entry for entry in reversed(json.loads(row['results'])) if entry.get('scope') == 'index_scope_v1'), None)
    reference = item.get('artifact') if isinstance(item, dict) else None
    if not isinstance(reference, dict):
        raise TypeError('候选范围任务缺少产物')
    sha, size = reference.get('sha256'), reference.get('bytes')
    if _sha(sha) is None or type(size) is not int or not 0 < size <= 64 * 1024 * 1024:
        raise ValueError('候选范围产物引用无效')
    root = db.parent / 'stock-updates' / 'artifacts'
    path = root / (str(sha) + '.json')
    if (path.is_symlink() or not path.is_file() or path.stat().st_size != size):
        raise ValueError('候选范围产物缺失或长度不符')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('候选范围产物摘要不符')
    document = _json(raw)
    if not isinstance(document, dict):
        raise TypeError('候选范围产物结构无效')
    return document, str(sha)


def _validate_scope(document: dict[str, Any]) -> dict[str, Any]:
    """校验指数范围完整：5 个指数齐全、样本数等于声明、公司与指数行一致。"""
    if document.get('version') not in {'official-index-scope-v1', 'stock-reference-scope-v1'}:
        raise ValueError('候选范围版本无效')
    if document.get('status') != 'complete' or document.get('scope_complete') is not True:
        raise ValueError('候选范围不完整')
    as_of = _day(document.get('as_of'))
    if as_of is None:
        raise ValueError('候选范围截点无效')
    companies = document.get('companies')
    if not isinstance(companies, list) or not companies:
        raise ValueError('候选范围公司清单无效')
    seen: set[str] = set()
    for row in companies:
        if (not isinstance(row, dict) or not isinstance(row.get('symbol'), str)
                or _SYMBOL.fullmatch(row['symbol']) is None or row['symbol'] in seen
                or not isinstance(row.get('name'), str) or not row['name'].strip()
                or not isinstance(row.get('source_indexes'), list) or not row['source_indexes']
                or len(set(row['source_indexes'])) != len(row['source_indexes'])
                or not isinstance(row.get('pool_ids'), list)):
            raise ValueError('候选范围公司身份无效')
        expected = {pool for pool, codes in POOL_INDEX_CODES.items()
                    if set(row['source_indexes']) & set(codes)}
        if set(row['pool_ids']) != expected:
            raise ValueError('候选范围池归属与指数来源不一致')
        seen.add(row['symbol'])
    items = document.get('items')
    if not isinstance(items, list) or not items:
        raise ValueError('候选范围缺少指数来源')
    codes = sorted({code for source in POOL_INDEX_CODES.values() for code in source})
    by_code: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or item.get('index_code') not in INDEX_SOURCES:
            raise ValueError('候选范围指数来源无效')
        code = str(item['index_code'])
        if code in by_code:
            raise ValueError('候选范围指数来源重复')
        by_code[code] = item
    if sorted(by_code) != codes:
        raise ValueError('候选范围指数来源不齐')
    rows: set[str] = set()
    for code, item in by_code.items():
        expected = INDEX_SOURCES[code]['count']
        if item.get('expected_count') != expected or item.get('complete') is not True:
            raise ValueError(f'候选范围 {code} 样本数或完整性声明不符')
        if item.get('row_count') != expected:
            raise ValueError(f'候选范围 {code} 原始行数不符')
        raw_rows, exclusions = item.get('rows'), item.get('scope_exclusions')
        if not isinstance(raw_rows, list) or not isinstance(exclusions, list):
            raise TypeError(f'候选范围 {code} 行情行无效')
        if len(raw_rows) + len(exclusions) != expected:
            raise ValueError(f'候选范围 {code} 行数与声明样本数不符')
        for row in raw_rows:
            if (not isinstance(row, dict) or not isinstance(row.get('symbol'), str)
                    or _SYMBOL.fullmatch(row['symbol']) is None):
                raise ValueError(f'候选范围 {code} 存在无效证券')
            rows.add(row['symbol'])
        for row in exclusions:
            if not isinstance(row, dict) or not isinstance(row.get('symbol'), str) or not row.get('reason'):
                raise ValueError(f'候选范围 {code} 排除项无效')
    if rows != seen:
        raise ValueError('候选范围公司与指数行不一致')
    pools = document.get('pools')
    if not isinstance(pools, dict) or set(pools) != set(POOL_INDEX_CODES):
        raise ValueError('候选范围池集合无效')
    for pool, specs in POOL_INDEX_CODES.items():
        entry = pools[pool]
        expected = {row['symbol'] for row in companies if pool in row['pool_ids']}
        if (not isinstance(entry, dict) or entry.get('complete') is not True
                or entry.get('index_codes') != list(specs)
                or not isinstance(entry.get('companies'), list)
                or set(entry['companies']) != expected):
            raise ValueError(f'候选范围 {pool} 池不完整')
    return {'as_of': as_of, 'companies': companies, 'symbols': sorted(seen)}


def build_peer_universe(scope_document: dict[str, Any], *, source_run_id: str, source_scope_sha256: str,
                        industry_mapping_sha256: str, created_at: str) -> dict[str, Any]:
    """把完整指数范围固化为独立同业范围，保存可复核来源摘要。"""
    if not isinstance(source_run_id, str) or not source_run_id.strip():
        raise ValueError('候选范围任务标识无效')
    if _sha(source_scope_sha256) is None or _sha(industry_mapping_sha256) is None:
        raise ValueError('同业范围来源摘要无效')
    checked = _validate_scope(scope_document)
    pools = ['dividend_value', 'growth_trend']
    frozen = reference_snapshot(scope_document, pools, checked['as_of'], source_run_id=source_run_id,
                                source_sha256=source_scope_sha256)
    companies = sorted(frozen['scope_document']['companies'], key=lambda row: row['symbol'])
    if [row['symbol'] for row in companies] != checked['symbols']:
        raise ValueError('同业范围公司与指数行不一致')
    payload = {
        'version': VERSION, 'universe': UNIVERSE, 'as_of': checked['as_of'].isoformat(),
        'source_run_id': source_run_id, 'source_scope_sha256': source_scope_sha256,
        'scope_sha256': frozen['scope_sha256'], 'industry_mapping_sha256': industry_mapping_sha256,
        'company_count': len(companies), 'selected_pool_ids': pools,
        'companies': companies, 'scope_document': frozen['scope_document'],
        'created_at': created_at,
    }
    payload['universe_sha256'] = _id({key: payload[key] for key in
                                      ('version', 'universe', 'as_of', 'source_scope_sha256',
                                       'scope_sha256', 'industry_mapping_sha256', 'company_count',
                                       'companies')})
    return payload


def save_peer_universe(path: Path, payload: dict[str, Any]) -> None:
    """原子写入；调用方负责先确认目录归属。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def _validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get('version') != VERSION or payload.get('universe') != UNIVERSE:
        raise ValueError('同业范围版本无效')
    as_of = _day(payload.get('as_of'))
    if as_of is None:
        raise ValueError('同业范围截点无效')
    if _sha(payload.get('source_scope_sha256')) is None or _sha(payload.get('scope_sha256')) is None \
            or _sha(payload.get('industry_mapping_sha256')) is None or _sha(payload.get('universe_sha256')) is None:
        raise ValueError('同业范围摘要无效')
    companies = payload.get('companies')
    count = payload.get('company_count')
    if not isinstance(companies, list) or not isinstance(count, int) or count != len(companies) or count < 2:
        raise ValueError('同业范围公司数量无效')
    symbols: list[str] = []
    for row in companies:
        if (not isinstance(row, dict) or not isinstance(row.get('symbol'), str)
                or _SYMBOL.fullmatch(row['symbol']) is None
                or not isinstance(row.get('name'), str) or not row['name'].strip()
                or not isinstance(row.get('pool_ids'), list) or not isinstance(row.get('source_indexes'), list)):
            raise ValueError('同业范围公司记录无效')
        symbols.append(row['symbol'])
    if len(set(symbols)) != len(symbols) or symbols != sorted(symbols):
        raise ValueError('同业范围公司清单不是唯一升序')
    expected = _id({key: payload[key] for key in
                    ('version', 'universe', 'as_of', 'source_scope_sha256',
                     'scope_sha256', 'industry_mapping_sha256', 'company_count', 'companies')})
    if expected != payload['universe_sha256']:
        raise ValueError('同业范围摘要不符')
    return {'as_of': as_of, 'symbols': symbols}


def load_peer_universe(state_dir: Path, *, path: Path | None = None) -> dict[str, Any]:
    """读取并复核同业范围；缺失或损坏返回 unavailable，不替代为小范围。"""
    result: dict[str, Any] = {'status': 'unavailable', 'payload': None, 'file_sha256': None,
                              'reason': '同业范围未准备'}
    target = Path(state_dir) / 'stock-leadership' / 'peer-universe.json' if path is None else Path(path)
    try:
        raw = target.read_bytes()
        payload = _json(raw)
        checked = _validate_payload(payload)
        result.update(status='current', payload=payload, file_sha256=hashlib.sha256(raw).hexdigest(),
                      as_of=checked['as_of'].isoformat(), company_count=len(checked['symbols']),
                      reason='同业范围已绑定')
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    return result


def peer_scope_document(payload: dict[str, Any]) -> dict[str, Any]:
    """返回可供 reference_snapshot 复用的冻结范围文档。"""
    checked = _validate_payload(payload)
    document = payload.get('scope_document')
    if not isinstance(document, dict) or not isinstance(document.get('companies'), list):
        raise TypeError('同业范围缺少范围文档')
    symbols = [row.get('symbol') for row in document['companies']]
    if len(symbols) != len(set(symbols)) or sorted(symbols) != checked['symbols']:
        raise ValueError('同业范围文档与公司清单不一致')
    return document


def frozen_reference_snapshot(payload: dict[str, Any], as_of: date) -> dict[str, Any]:
    """按同业范围重建指定截点的基础资料冻结范围。"""
    document = peer_scope_document(payload)
    return reference_snapshot(document, ['dividend_value', 'growth_trend'], as_of,
                              source_run_id=str(payload['source_run_id']),
                              source_sha256=str(payload['source_scope_sha256']))


def industry_mapping_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now_text() -> str:
    return datetime.now().astimezone().isoformat()

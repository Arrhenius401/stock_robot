"""风险警示板原文只读研究线索；不产生审核事实或资格结论。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import requests
from filelock import FileLock, Timeout

from radar.provider_request import ProviderRequestError, bounded_provider_call
from radar.stocks.admission import _BJ
from radar.stocks.market_history import _atomic
from radar.stocks.market_value_registry import _bytes, _path, _reference
from radar.stocks.research_store import _symbol
from radar.stocks.structured_collect import _json

logger = logging.getLogger(__name__)
URL = 'https://40.push2.eastmoney.com/api/qt/clist/get'
VERSION = 'risk-warning-v2'
_PARAMS = {'pz': '100', 'po': '1', 'np': '1', 'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
           'fltt': '2', 'invt': '2', 'fid': 'f12', 'fs': 'm:0 f:4,m:1 f:4', 'fields': 'f12,f13,f14'}


def _download_page(page: int) -> tuple[int, bytes]:
    response = requests.get(URL, params={**_PARAMS, 'pn': str(page)}, timeout=15, stream=True,
                            allow_redirects=False)
    try:
        if response.status_code != 200:
            return response.status_code, b''
        body = bytearray()
        for chunk in response.iter_content(65536):
            body.extend(chunk)
            if len(body) > 4 * 1024 * 1024:
                raise ValueError('风险名单原文超出预算')
        return response.status_code, bytes(body)
    finally:
        response.close()


def _fetch_page(page: int) -> tuple[int, bytes]:
    return bounded_provider_call(__name__, '_download_page', args=(page,), timeout=15)


def collect_risk_warning(state_dir: Path, *, fetch: Callable[[int], tuple[int, bytes]] = _fetch_page,
                         sleep: Callable[[float], Any] = time.sleep) -> dict[str, Any]:
    """保留限速原文；失败立即失效旧快照，不修改业务数据库。"""
    root = Path(state_dir).absolute() / 'stock-risk-warning'
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError('风险名单目录不能经过符号链接')
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / '.collect.lock'
    if lock_path.is_symlink():
        raise ValueError('风险名单锁不能是符号链接')
    try:
        with FileLock(str(lock_path), timeout=0):
            return _collect_risk_warning_locked(state_dir, root, fetch, sleep)
    except Timeout:
        return {'status': 'failed', 'reason': '风险名单采集锁已存在，需等待或检查中断任务',
                'research_only': True, 'formal_screening_enabled': False}


def _collect_risk_warning_locked(state_dir: Path, root: Path, fetch: Callable[[int], tuple[int, bytes]],
                                 sleep: Callable[[float], Any]) -> dict[str, Any]:
    manifest_path = root / 'manifest.json'
    started = datetime.now(_BJ)
    failed = {'version': VERSION, 'status': 'failed', 'as_of': started.date().isoformat()}
    _atomic(manifest_path, json.dumps(failed).encode())
    refs: list[dict[str, Any]] = []
    try:
        expected = None
        for page in range(1, 21):
            if page > 1:
                sleep(1)
            status, raw = fetch(page)
            if status != 200:
                raise ValueError(f'风险名单HTTP{status}，停止采集')
            if not isinstance(raw, bytes) or not 0 < len(raw) <= 4 * 1024 * 1024:
                raise ValueError('风险名单原文无效')
            payload = _json(raw)
            if payload.get('rc') != 0:
                raise ValueError('风险名单来源返回错误')
            data = payload['data']
            total, rows = data['total'], data['diff']
            if (type(total) is not int or not 0 < total <= 2000 or not isinstance(rows, list)
                    or (expected is not None and total != expected)):
                raise ValueError('风险名单总数变化或无效')
            expected = total
            sha = hashlib.sha256(raw).hexdigest()
            name = f'{sha}.json'
            _atomic(root / name, raw)
            refs.append({'path': name, 'bytes': len(raw), 'sha256': sha})
            if page * 100 >= total:
                break
        captured = datetime.now(_BJ)
        if captured.date() != started.date():
            raise ValueError('风险名单采集跨日期')
        manifest = {'version': VERSION, 'as_of': captured.date().isoformat(), 'source_url': URL,
                    'fetched_at': captured.isoformat(), 'pages': refs}
        _atomic(manifest_path, json.dumps(manifest, ensure_ascii=False).encode())
        result = load_risk_warning_source(state_dir, '600000', captured.date(), _allow_locked=True)
        if result['status'] != 'current':
            raise ValueError('风险名单原文复读失败')
        return {'status': 'complete', 'as_of': captured.date().isoformat(), 'pages': len(refs),
                'research_only': True, 'formal_screening_enabled': False}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, ProviderRequestError, requests.RequestException) as exc:
        logger.warning('风险名单采集失败: %s', exc)
        _atomic(manifest_path, json.dumps(failed).encode())
        return {**failed, 'reason': str(exc), 'research_only': True, 'formal_screening_enabled': False}


def load_risk_warning_source(state_dir: Path, symbol: str, as_of: date, *, _allow_locked: bool = False) -> dict[str, Any]:
    """复核全部原文和采集时点；未命中不代表无风险。"""
    symbol = _symbol(symbol)
    if type(as_of) is not date:
        raise ValueError('研究截点无效')
    root = Path(state_dir).absolute() / 'stock-risk-warning'
    if _allow_locked or not root.is_dir():
        return _load_risk_warning_source_locked(state_dir, symbol, as_of)
    if any(path.is_symlink() for path in (root, *root.parents, root / '.collect.lock')):
        return {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'unavailable',
                'observation': 'unknown', 'reviewed': False, 'formal_screening_enabled': False}
    try:
        with FileLock(str(root / '.collect.lock'), timeout=3):
            return _load_risk_warning_source_locked(state_dir, symbol, as_of)
    except (Timeout, OSError):
        return {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'unavailable',
                'observation': 'unknown', 'reviewed': False, 'formal_screening_enabled': False}


def _load_risk_warning_source_locked(state_dir: Path, symbol: str, as_of: date) -> dict[str, Any]:
    out: dict[str, Any] = {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'unavailable',
                           'observation': 'unknown', 'reviewed': False, 'formal_screening_enabled': False}
    try:
        root = Path(state_dir).absolute() / 'stock-risk-warning'
        manifest_path = _path(root, 'manifest.json')
        raw_manifest = _bytes(manifest_path)
        manifest = _json(raw_manifest)
        captured = datetime.fromisoformat(manifest['fetched_at'])
        snapshot_day = date.fromisoformat(manifest['as_of'])
        if (manifest.get('version') != VERSION or manifest.get('source_url') != URL
                or captured.utcoffset() is None or captured > datetime.now(UTC)
                or captured.astimezone(_BJ).date() != snapshot_day or snapshot_day > as_of):
            raise ValueError('风险名单来源或时间无效')
        pages = manifest.get('pages')
        if not isinstance(pages, list) or not 1 <= len(pages) <= 20:
            raise ValueError('风险名单分页数量无效')
        items: list[dict[str, Any]] = []
        original: list[tuple[Path, bytes]] = []
        expected = None
        for ref in pages:
            path, raw = _reference(root, ref, need_size=True)
            payload = _json(raw)
            if payload.get('rc') != 0:
                raise ValueError('风险名单来源返回错误')
            data = payload['data']
            total, rows = data['total'], data['diff']
            if (type(total) is not int or not 0 < total <= 2000 or not isinstance(rows, list)
                    or (expected is not None and total != expected)):
                raise ValueError('风险名单总数或分页无效')
            expected = total
            original.append((path, raw))
            items.extend(rows)
        if len(items) != expected:
            raise ValueError('风险名单分页不完整')
        seen: set[tuple[str, int]] = set()
        found = None
        for row in items:
            if not isinstance(row, dict):
                raise TypeError('风险名单存在无效记录')
            code, market, name = row.get('f12'), row.get('f13'), row.get('f14')
            if (not isinstance(code, str) or re.fullmatch(r'\d{6}', code) is None
                    or type(market) is not int or market not in (0, 1)
                    or (code.startswith('6') and market != 1)
                    or (code.startswith(('000', '001', '002', '003', '300', '301', '302')) and market != 0)
                    or not isinstance(name, str) or not name.strip()):
                raise ValueError('风险名单证券身份无效')
            identity = (code, market)
            if identity in seen:
                raise ValueError('风险名单证券重复')
            seen.add(identity)
            if code == symbol and market == (1 if symbol.startswith('6') else 0):
                found = name
        if _bytes(manifest_path) != raw_manifest or any(_bytes(path) != raw for path, raw in original):
            raise ValueError('风险名单读取期间变化')
        out.update(status='current' if snapshot_day == as_of else 'historical',
                   observation='observed' if found else 'not_observed', source_name=found,
                   snapshot_date=snapshot_day.isoformat(), fetched_at=captured.isoformat(),
                   source_url=URL, page_sha256=[ref['sha256'] for ref in pages],
                   reason='仅为名单观察结果；未命中不代表没有风险或治理已审核')
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, json.JSONDecodeError) as exc:
        logger.debug('风险名单不可用: %s', exc)
    return out

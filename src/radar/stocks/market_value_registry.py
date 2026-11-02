"""只读登记市值来源；重新核对来源、日历及文件身份。"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from radar.calendar import CalendarUnavailable, TradingCalendar
from radar.stocks.admission import _day
from radar.stocks.market_context import prepare_market_window
from radar.stocks.market_value_source import evaluate_supplier_market_value
from radar.stocks.structured_collect import _json, _safe_path

_VERSION = 'stock-market-value-registry-v1'
_LIMIT = 8 * 1024 * 1024
_CODES = ('capitalization_current', 'capitalization_average_60d')


def _bytes(path: Path) -> bytes:
    with path.open('rb') as stream:
        data = stream.read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise ValueError('来源超出大小限制')
    return data


def _path(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('来源路径无效')
    relative = Path(value)
    if relative.is_absolute() or any(part in ('.', '..') for part in value.split('/')):
        raise ValueError('来源路径必须在登记目录内')
    target = root
    for part in relative.parts:
        target = target / part
        if target.is_symlink() or (hasattr(target, 'is_junction') and target.is_junction()):
            raise ValueError('来源路径不能含链接')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('来源路径越界')
    _safe_path(target)
    return target


def _reference(root: Path, entry: Any, *, need_size: bool = False) -> tuple[Path, bytes]:
    if not isinstance(entry, dict):
        raise TypeError('来源引用无效')
    digest = entry.get('sha256')
    if not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
        raise ValueError('来源摘要无效')
    path = _path(root, entry.get('path'))
    data = _bytes(path)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('来源摘要不匹配')
    if need_size and (type(entry.get('bytes')) is not int or entry['bytes'] != len(data)):
        raise ValueError('响应长度不匹配')
    return path, data


def _unavailable(symbol: str, as_of: date, digest: str | None = None) -> dict[str, Any]:
    return {'symbol': symbol, 'evaluation_as_of': as_of.isoformat(), 'as_of': None, 'status': 'unavailable',
            'manifest_sha256': digest, 'source': {}, 'checks': [
                {'code': code, 'outcome': 'pending_verification', 'reason': '供应商市值来源缺失或校验失败'}
                for code in _CODES]}


def _registered_load_market_value_source(state_dir: Path, symbol: str, as_of: date, manifest_file: Path | None = None) -> dict[str, Any]:
    """只读固定登记文件；损坏、替换或日历不符时不使用结果。"""
    if type(as_of) is not date:
        raise ValueError('评估日期无效')
    digest = None
    try:
        root = Path(state_dir).absolute() / 'stock-market-value'
        if root.is_symlink() or (hasattr(root, 'is_junction') and root.is_junction()):
            raise ValueError('登记目录不能是链接')
        if manifest_file is not None:
            root = manifest_file.parent
        manifest_path = _path(root, manifest_file.name if manifest_file is not None else 'manifest.json')
        raw = _bytes(manifest_path)
        digest = hashlib.sha256(raw).hexdigest()
        manifest = _json(raw)
        if not isinstance(manifest, dict) or manifest.get('version') != _VERSION:
            raise ValueError('登记清单版本无效')
        target = _day(manifest.get('as_of'))
        if (target is None or target > as_of or not isinstance(symbol, str)
                or re.fullmatch(r'(?:60[0135]|688|000|001|002|003|30[012])\d{3}', symbol) is None):
            raise ValueError('登记身份或截点无效')
        companies = manifest.get('companies')
        if not isinstance(companies, dict) or not isinstance(companies.get(symbol), list) or not companies[symbol]:
            raise ValueError('公司未登记')
        scope_entry, window_entry = manifest.get('scope'), manifest.get('window')
        if not isinstance(scope_entry, dict) or not isinstance(window_entry, dict):
            raise TypeError('范围或窗口引用无效')
        scope_path, scope_bytes = _reference(root, scope_entry)
        window_path, window_bytes = _reference(root, window_entry)
        registered_window = _json(window_bytes)
        calendar = TradingCalendar(Path(state_dir) / 'radar_collector.db', refresh_enabled=False)
        now = datetime.now().astimezone()
        expected = prepare_market_window(calendar, target, now=now)
        if (not isinstance(registered_window, dict) or registered_window.get('days') != expected.get('days')
                or not isinstance(registered_window.get('calendar'), dict)
                or registered_window['calendar'].get('snapshot_sha256') != expected['calendar']['snapshot_sha256']):
            raise ValueError('登记窗口与缓存日历不一致')
        artifacts = []
        originals = []
        for entry in companies[symbol]:
            path, data = _reference(root, entry, need_size=True)
            artifacts.append({**entry, 'path': path})
            originals.append((entry, data))
        computed = evaluate_supplier_market_value(symbol=symbol, as_of=target, scope_path=scope_path,
                                                  scope_sha256=scope_entry['sha256'], window_path=window_path,
                                                  window_sha256=window_entry['sha256'], artifacts=artifacts)
        if (not isinstance(computed, dict) or computed.get('status') != 'current'
                or computed.get('symbol') != symbol or computed.get('as_of') != target.isoformat()):
            raise ValueError('来源计算失败')
        if _bytes(manifest_path) != raw:
            raise ValueError('登记清单已变化')
        if _reference(root, scope_entry)[1] != scope_bytes or _reference(root, window_entry)[1] != window_bytes:
            raise ValueError('范围或窗口已变化')
        if any(_reference(root, entry, need_size=True)[1] != data for entry, data in originals):
            raise ValueError('响应已变化')
        after = prepare_market_window(calendar, target, now=now)
        if after.get('days') != expected['days'] or after['calendar']['snapshot_sha256'] != expected['calendar']['snapshot_sha256']:
            raise ValueError('日历已变化')
        return {'symbol': symbol, 'evaluation_as_of': as_of.isoformat(), 'as_of': target.isoformat(),
                'status': 'current' if target == as_of else 'historical', 'manifest_sha256': digest,
                'window_days': expected['days'], 'calendar_sha256': expected['calendar']['snapshot_sha256'],
                'source': computed.get('source', {}), 'checks': computed.get('checks', [])}
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, CalendarUnavailable):
        return _unavailable(symbol, as_of, digest)


def load_market_value_source(state_dir: Path, symbol: str, as_of: date, *, db: Path | None = None) -> dict[str, Any]:
    """基础资料新任务优先，无适用任务时兼容旧登记。"""
    from radar.stocks.reference_task import select_reference_manifest

    state_dir = Path(state_dir).absolute()
    path, identity = select_reference_manifest(state_dir, symbol, as_of, 'market_value', db=db)
    if identity is None:
        return _registered_load_market_value_source(state_dir, symbol, as_of)
    result = _registered_load_market_value_source(state_dir, symbol, as_of, path) if path is not None else _unavailable(symbol, as_of)
    after_path, after = select_reference_manifest(state_dir, symbol, as_of, 'market_value', db=db)
    if after_path != path or after != identity:
        result = _unavailable(symbol, as_of)
        identity = after or identity
    result['reference_task'] = identity
    if result.get('status') == 'unavailable':
        result['reason'] = identity['reason'] if path is None else '最新基础资料任务内容未通过校验，不能回退旧登记'
        for check in result.get('checks', []):
            check['reason'] = result['reason']
    return result

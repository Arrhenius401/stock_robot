"""共享证据：交易所监管快照与同业规模排名的一次准备、多公司复用。

只做产物准备与复核，不改判定规则；缺失时明确不可用，绝不回退为更小范围。
"""
from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from radar.stocks.disclosure_evaluation import EVALUATION_PATH
from radar.stocks.exchange_governance import (
    collect_exchange_governance,
    verify_exchange_governance,
)
from radar.stocks.industry_scale import (
    build_peer_scale_from_state,
    load_industry_scale,
    save_industry_scale,
)
from radar.stocks.peer_universe import industry_mapping_sha256

VERSION = 'shared-evidence-v1'


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def governance_ready(state_dir: Path, as_of: date) -> dict[str, Any] | None:
    """同日且原文复核通过的监管快照才可复用。"""
    verified = verify_exchange_governance(state_dir)
    if verified.get('status') != 'current' or verified.get('as_of') != as_of.isoformat():
        return None
    return {'current': True, 'as_of': verified['as_of'], 'manifest_sha256': verified.get('manifest_sha256'),
            'records': verified.get('records'), 'reused': True, 'reason': '复用同日监管快照'}


def archive_governance(state_dir: Path) -> str | None:
    """采集前保留上一份已验证快照；失败刷新不销毁历史证据。"""
    root = Path(state_dir).absolute() / 'stock-exchange-governance'
    manifest = root / 'manifest.json'
    try:
        payload = json.loads(manifest.read_bytes())
        if payload.get('status') != 'complete' or not isinstance(payload.get('as_of'), str):
            return None
        target = root / 'archive' / payload['as_of']
        if target.exists():
            return str(target)
        files = [manifest, root / 'index.json', *sorted(root.glob('sse-*.json')), *sorted(root.glob('szse-*.json'))]
        target.mkdir(parents=True, exist_ok=True)
        for source in files:
            if source.is_file():
                shutil.copy2(source, target / source.name)
        return str(target)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def collect_governance(state_dir: Path, as_of: date,
                       progress: Any | None = None) -> dict[str, Any]:
    """采集当日监管快照；已有同日快照则直接复用，失败保留旧快照与暂存页面。"""
    ready = governance_ready(state_dir, as_of)
    if ready is not None:
        return ready
    archived = archive_governance(state_dir)
    on_page: Callable[[int, int, str], None] | None = None
    if progress is not None:
        def report_page(page: int, total: int, key: str) -> None:
            progress(page, total, [{'scope': f'governance:{key}', 'status': 'running', 'summary': ''}],
                     'governance')
        on_page = report_page
    result = collect_exchange_governance(state_dir, on_page=on_page)
    if result.get('status') != 'complete':
        return {'current': False, 'as_of': as_of.isoformat(), 'archived_to': archived,
                'staged': result.get('staged'),
                'reason': str(result.get('reason') or '监管快照采集失败')}
    verified = verify_exchange_governance(state_dir)
    if verified.get('status') != 'current' or verified.get('as_of') != as_of.isoformat():
        return {'current': False, 'as_of': as_of.isoformat(), 'archived_to': archived,
                'reason': '监管快照日期或原文复核不符'}
    return {'current': True, 'as_of': verified['as_of'], 'manifest_sha256': verified.get('manifest_sha256'),
            'records': verified.get('records'), 'reused': False, 'archived_to': archived,
            'reason': '监管快照已采集并复核'}


def scale_ready(state_dir: Path, as_of: date, *, peer_universe_sha256: str, company_count: int,
                sample_symbol: str) -> dict[str, Any] | None:
    """同日、同同业范围且成员数一致的排名文件才可复用。"""
    loaded = load_industry_scale(state_dir, sample_symbol, as_of)
    if loaded.get('status') != 'current' or loaded.get('version') != 'industry-scale-v2':
        return None
    if loaded.get('company_count') != company_count or loaded.get('peer_universe_sha256') != peer_universe_sha256:
        return None
    return {'current': True, 'as_of': as_of.isoformat(), 'file_sha256': loaded.get('file_sha256'),
            'company_count': company_count, 'reused': True, 'reason': '复用同截点同业排名'}


def build_scale(state_dir: Path, db: Path, as_of: date, *, peer_universe_sha256: str, scope_sha256: str,
                symbols: list[str], window_sha256: str, mapping_path: Path) -> dict[str, Any]:
    """生成 v2 排名；成员不齐、市值日期不一致或行业缺失时失败，不降级。"""
    try:
        payload = build_peer_scale_from_state(
            state_dir, db, as_of, symbols, peer_universe_sha256=peer_universe_sha256,
            scope_sha256=scope_sha256, industry_mapping_sha256=industry_mapping_sha256(mapping_path),
            window_sha256=window_sha256)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {'current': False, 'as_of': as_of.isoformat(), 'reason': f'同业排名生成失败：{type(exc).__name__}: {exc}'}
    path = Path(state_dir) / 'stock-leadership' / 'scale.json'
    save_industry_scale(path, payload)
    loaded = load_industry_scale(state_dir, symbols[0], as_of)
    if loaded.get('status') != 'current':
        return {'current': False, 'as_of': as_of.isoformat(), 'reason': '同业排名写入后复核失败'}
    return {'current': True, 'as_of': as_of.isoformat(), 'file_sha256': loaded.get('file_sha256'),
            'company_count': payload['company_count'], 'market_cap_date': payload['market_cap_date'],
            'reused': False, 'reason': '同业排名已生成并复核'}


def evaluation_identity(as_of: date) -> dict[str, Any]:
    """交易所信息披露评价文件的只读身份；期间与发布日一并展示。"""
    try:
        raw = EVALUATION_PATH.read_bytes()
        payload = json.loads(raw)
        published = date.fromisoformat(str(payload.get('published_on')))
        return {'current': published <= as_of, 'file_sha256': hashlib.sha256(raw).hexdigest(),
                'published_on': published.isoformat(), 'period_start': payload.get('period_start'),
                'period_end': payload.get('period_end')}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {'current': False, 'file_sha256': None, 'reason': '交易所评价文件不可用'}


def evidence_identities(state_dir: Path, as_of: date, *, peer_universe_sha256: str | None = None,
                        scale_file_sha256: str | None = None) -> dict[str, Any]:
    """筛选快照绑定的共享证据身份，供详情与计划复核。"""
    governance = verify_exchange_governance(state_dir)
    return {'version': VERSION, 'as_of': as_of.isoformat(),
            'governance': {'status': governance.get('status'), 'as_of': governance.get('as_of'),
                           'manifest_sha256': governance.get('manifest_sha256'),
                           'records': governance.get('records')},
            'industry_scale': {'file_sha256': scale_file_sha256, 'peer_universe_sha256': peer_universe_sha256},
            'evaluation': evaluation_identity(as_of), 'generated_at': _now()}

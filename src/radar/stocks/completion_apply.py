"""补全执行：读取计划、校验哈希、按 --yes 调用三个适配器，输出证据记录。

本地证据入口：
- 年报现金流：<annual_dir>/<symbol>-<year>.txt（或 .pdf）
- 成交额：<turnover_dir>/<symbol>.json，[{date, amount_yuan}, ...]
- 来源重试：<retry_dir>/<symbol>.json，{"status": "complete", ...}
不联网、不调用模型、不直接改股票池。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from radar.stocks.completion_adapters import ADAPTERS, AdapterResult

APPLY_VERSION = 'completion-apply-2026-11-10-v1'


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


def _annual_text(directory: Path | None, symbol: str, years: list[int]) -> dict[int, str] | None:
    if directory is None:
        return None
    texts = {}
    for year in years:
        text_path = directory / f'{symbol}-{year}.txt'
        pdf_path = directory / f'{symbol}-{year}.pdf'
        if text_path.is_file():
            texts[year] = text_path.read_text(encoding='utf-8')
        elif pdf_path.is_file():
            from pypdf import PdfReader

            texts[year] = '\n'.join(page.extract_text() or '' for page in PdfReader(str(pdf_path)).pages)
        else:
            raise ValueError(f'缺少年报原文：{pdf_path.name}')
    return texts


def _turnover_rows(directory: Path | None, symbol: str, as_of: date) -> list[dict[str, Any]] | None:
    if directory is None:
        return None
    path = directory / f'{symbol}.json'
    if not path.is_file():
        raise ValueError(f'缺少成交额证据：{path.name}')
    rows = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(rows, list):
        raise TypeError('成交额证据必须是列表')
    return rows


def _source_result(directory: Path | None, symbol: str, as_of: date) -> dict[str, Any] | None:
    if directory is None:
        return None
    path = directory / f'{symbol}.json'
    if not path.is_file():
        raise ValueError(f'缺少来源重试证据：{path.name}')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise TypeError('来源重试证据必须是对象')
    return value


def apply_plan(plan: dict[str, Any], *, select: set[str] | None = None, dry_run: bool = False,
               yes: bool = False, annual_dir: Path | None = None, turnover_dir: Path | None = None,
               retry_dir: Path | None = None,
               providers: dict[str, Callable[..., Any]] | None = None) -> dict[str, Any]:
    """执行选中的计划项；默认 dry-run，只有 yes=True 才读取证据并产出 applied 结果。"""
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
            if source_kind == 'annual_report_cashflow':
                fetch = (providers or {}).get('annual_report') or (
                    lambda symbol, values, directory=annual_dir: _annual_text(directory, symbol, values))
                result: AdapterResult = adapter.collect(item, date.fromisoformat(plan['as_of']), fetch=fetch)
            elif source_kind == 'market_turnover_60d':
                fetch = (providers or {}).get('turnover') or (
                    lambda symbol, as_of, directory=turnover_dir: _turnover_rows(directory, symbol, as_of))
                result = adapter.collect(item, date.fromisoformat(plan['as_of']), fetch=fetch)
            elif source_kind == 'dividend_source_retry':
                retry = (providers or {}).get('source_retry') or (
                    lambda symbol, as_of, directory=retry_dir: _source_result(directory, symbol, as_of))
                result = adapter.collect(item, date.fromisoformat(plan['as_of']), retry=retry)
            else:
                result = AdapterResult('pending_verification', reason=f'未知来源 {source_kind}')
        except Exception as exc:  # noqa: BLE001 数据源边界
            result = AdapterResult('pending_verification', reason=f'证据不可用：{type(exc).__name__}: {exc}')
        run['items'].append({'symbol': item.get('symbol'), 'condition': item.get('condition'),
                             'source_kind': source_kind, **result.as_dict()})
    return run

"""从已校验的结构化财务批次生成只读成长研究报告。"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from radar.stocks.growth_research import calculate_growth_research
from radar.stocks.market_value_registry import _bytes, _path, _reference
from radar.stocks.market_value_source import _scope
from radar.stocks.structured_collect import _json, _safe_path

logger = logging.getLogger(__name__)


def load_growth_source(state_dir: Path, symbol: str, as_of: date) -> dict[str, Any]:
    """登记原始主要指标响应，历史结果只作参考，不读取派生通过标志。"""
    source: dict[str, Any] = {'symbol': symbol, 'evaluation_as_of': as_of.isoformat(),
                              'status': 'unavailable', 'as_of': None, 'research': None, 'manifest_sha256': None}
    try:
        root = state_dir.absolute() / 'stock-growth'
        manifest_path = _path(root, 'manifest.json')
        raw = _bytes(manifest_path)
        source['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
        manifest = _json(raw)
        if manifest['version'] != 'stock-growth-source-v1':
            raise ValueError('成长来源版本不符')
        cutoff = date.fromisoformat(manifest['as_of'])
        if cutoff > as_of:
            raise ValueError('成长来源晚于评估截点')
        scope_path, scope_raw = _reference(root, manifest['scope'], need_size=True)
        _scope(_json(scope_raw), symbol, cutoff)
        ref = manifest['companies'][symbol]
        path, body = _reference(root, ref, need_size=True)
        fetched = datetime.fromisoformat(ref['fetched_at'])
        if (fetched.tzinfo is None or fetched > datetime.now(UTC)
                or ref['source_url'] != 'https://datacenter.eastmoney.com/securities/api/data/get'
                or ref['request_params']['type'] != 'RPT_F10_FINANCE_MAINFINADATA'
                or ref['request_params']['filter'] != f'(SECUCODE="{symbol}.{"SH" if symbol.startswith("6") else "SZ"}")'):
            raise ValueError('成长来源端点、身份或时间不符')
        payload = _json(body)
        page = payload['result']
        if payload.get('success') is not True or page['pages'] != 1 or page['count'] != len(page['data']):
            raise ValueError('成长响应分页不完整')
        rows = page['data']
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('成长原始记录格式无效')
        periods = [date.fromisoformat(str(row['REPORT_DATE'])[:10]) for row in rows
                   if row.get('SECURITY_CODE') == symbol and row.get('NOTICE_DATE')
                   and str(row['NOTICE_DATE'])[:10] <= cutoff.isoformat()
                   and str(row['REPORT_DATE'])[:10] <= cutoff.isoformat()]
        research = calculate_growth_research(symbol, rows, cutoff, max(periods))
        if _bytes(manifest_path) != raw or scope_path.read_bytes() != scope_raw or path.read_bytes() != body:
            raise ValueError('成长资料读取期间变化')
        source.update(status='current' if cutoff == as_of else 'historical', as_of=cutoff.isoformat(),
                      research=research, raw_sha256=ref['sha256'], scope_sha256=manifest['scope']['sha256'],
                      fetched_at=ref['fetched_at'])
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        logger.debug('成长登记资料不可用: %s', exc)
    return source


def build_growth_report(scope_path: Path, summary_path: Path) -> dict[str, Any]:
    """范围与原始内容摘要均复读；不采集、不修改研究库。"""
    from stock_robot.stock_structured_financial import screen_saved_batch

    root = summary_path.resolve().parent
    manifest_path = root / 'manifest.json'
    for path in (scope_path, summary_path, manifest_path):
        _safe_path(path.absolute())
    scope_raw, summary_raw, manifest_raw = scope_path.read_bytes(), summary_path.read_bytes(), manifest_path.read_bytes()
    scope, summary = _json(scope_raw), _json(summary_raw)
    verified = screen_saved_batch(summary_path)
    if hashlib.sha256(scope_raw).hexdigest() != verified['input_sha256']:
        raise ValueError('成长研究范围与原始财务输入不匹配')
    cutoff = date.fromisoformat(verified['as_of'])
    companies = scope['companies']
    _scope(scope, companies[0]['symbol'], cutoff)
    expected = {row['symbol']: row['name'] for row in companies}
    if expected != {row['symbol']: row['name'] for row in _json(manifest_raw)['companies']}:
        raise ValueError('成长研究公司范围与财务清单不一致')
    results = []
    for item in summary['items']:
        symbol = item['symbol']
        result: dict[str, Any] = {'symbol': symbol, 'status': 'unavailable', 'research': None}
        try:
            ref = item['artifact']
            artifact = root / 'artifacts' / (ref['sha256'] + '.json')
            _safe_path(artifact)
            raw = artifact.read_bytes()
            if len(raw) != ref['bytes'] or hashlib.sha256(raw).hexdigest() != ref['sha256']:
                raise ValueError('财务产物摘要不符')
            collection = _json(raw)['collection']
            if collection['symbol'] != symbol or collection['as_of'] != cutoff.isoformat():
                raise ValueError('财务身份或截点不符')
            main = collection['sources']['main']
            fetched = datetime.fromisoformat(main['fetched_at'])
            identity = f'{symbol}.{"SH" if symbol.startswith("6") else "SZ"}'
            if (fetched.tzinfo is None or fetched > datetime.now(UTC)
                    or main['request_params']['filter'] != f'(SECUCODE="{identity}")'):
                raise ValueError('主要指标来源时间或身份无效')
            digest = main['content_sha256']
            path = root / 'cache' / (digest + '.raw')
            _safe_path(path)
            if (Path(main['raw_artifact']).resolve() != path.resolve()
                    or main['source_url'] != 'https://datacenter.eastmoney.com/securities/api/data/get'
                    or main['request_params']['type'] != 'RPT_F10_FINANCE_MAINFINADATA'):
                raise ValueError('主要指标来源或路径不符')
            body = path.read_bytes()
            if len(body) != main['bytes'] or hashlib.sha256(body).hexdigest() != digest:
                raise ValueError('主要指标响应摘要不符')
            payload = _json(body)
            page = payload['result']
            if payload.get('success') is not True or page['pages'] != 1 or page['count'] != len(page['data']):
                raise ValueError('主要指标响应不是完整单页')
            research = calculate_growth_research(symbol, page['data'], cutoff, date.fromisoformat(collection['latest_available_period']))
            if path.read_bytes() != body or artifact.read_bytes() != raw:
                raise ValueError('成长来源读取期间发生变化')
            result.update(status='available', research=research, raw_sha256=digest,
                          artifact_sha256=ref['sha256'], fetched_at=main['fetched_at'])
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            logger.warning('成长资料校验失败 %s: %s', symbol, exc)
        results.append(result)
    seen = {row['symbol'] for row in results}
    results.extend({'symbol': symbol, 'status': 'unavailable', 'research': None} for symbol in expected if symbol not in seen)
    if (scope_path.read_bytes() != scope_raw or summary_path.read_bytes() != summary_raw
            or manifest_path.read_bytes() != manifest_raw):
        raise ValueError('成长研究输入读取期间发生变化')
    return {'as_of': cutoff.isoformat(), 'scope_sha256': hashlib.sha256(scope_raw).hexdigest(),
            'summary_sha256': hashlib.sha256(summary_raw).hexdigest(), 'items': results,
            'research_only': True, 'formal_screening_enabled': False, 'members_published': 0}

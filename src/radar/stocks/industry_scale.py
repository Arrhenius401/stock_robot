"""候选池内同行业市值排名：只作规模地位，不冒充市场份额或全市场龙头。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

VERSION = 'industry-scale-v1'
UNIVERSE = 'candidate_pool_626'
THRESHOLD = 3
MIN_PEERS = 5


def _amount(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _canonical(symbols: dict[str, Any]) -> bytes:
    return json.dumps(symbols, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def build_industry_scale(rows: list[dict[str, Any]], *, as_of: date, source: dict[str, Any] | None = None,
                         threshold: int = THRESHOLD, min_peers: int = MIN_PEERS) -> dict[str, Any]:
    """按申万二级和当前总市值排序；缺失市值保留 rank=None，不按 0 参与排名。"""
    if type(threshold) is not int or threshold < 1 or type(min_peers) is not int or min_peers < 1:
        raise ValueError('市值排名阈值无效')
    groups: dict[str, list[tuple[str, float]]] = {}
    for row in rows:
        symbol, industry = row.get('symbol'), row.get('industry')
        if not isinstance(symbol, str) or re.fullmatch(r'\d{6}', symbol) is None:
            raise ValueError('市值排名证券代码无效')
        if not isinstance(industry, str) or not industry.strip():
            raise ValueError('市值排名行业缺失')
        amount = _amount(row.get('market_cap_yuan'))
        if amount is not None:
            groups.setdefault(industry, []).append((symbol, amount))
    symbols: dict[str, Any] = {}
    for row in rows:
        symbol, industry = row['symbol'], row['industry']
        group = sorted(groups.get(industry, []), key=lambda item: (-item[1], item[0]))
        rank = next((index for index, item in enumerate(group, 1) if item[0] == symbol), None)
        amount = _amount(row.get('market_cap_yuan'))
        symbols[symbol] = {'industry': industry, 'rank': rank, 'peer_count': len(group),
                           'market_cap_yuan': str(amount) if amount is not None else None,
                           'market_cap_date': row.get('market_cap_date'),
                           'market_value_manifest_sha256': row.get('market_value_manifest_sha256')}
    payload = {'version': VERSION, 'as_of': as_of.isoformat(), 'universe': UNIVERSE,
               'threshold': threshold, 'min_peers': min_peers,
               'source': dict(source or {}), 'source_sha256': hashlib.sha256(_canonical(symbols)).hexdigest(),
               'symbols': symbols}
    return payload


def save_industry_scale(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')


def build_from_state(state_dir: Path, db: Path, as_of: date, symbols: list[str], *,
                     scope_sha256: str, industry_mapping_sha256: str) -> dict[str, Any]:
    """从同截点市值与行业来源生成排名；调用方负责把结果写入 scale.json。"""
    from radar.stocks.industry_source import load_industry_source
    from radar.stocks.market_value_registry import load_market_value_source

    rows = []
    manifests = {}
    for symbol in symbols:
        market = load_market_value_source(state_dir, symbol, as_of, db=db)
        manifests[symbol] = market.get('manifest_sha256') or ''
        cap = cap_date = None
        for check in market.get('checks') or []:
            if check.get('code') == 'capitalization_current':
                values = check.get('values') or {}
                cap, cap_date = values.get('amount_yuan'), values.get('date')
        industry = load_industry_source(state_dir, symbol, as_of, db=db).get('industry') or {}
        rows.append({'symbol': symbol, 'industry': industry.get('level2') or '',
                     'market_cap_yuan': cap, 'market_cap_date': cap_date,
                     'market_value_manifest_sha256': manifests[symbol]})
    source = {'universe': UNIVERSE, 'scope_sha256': scope_sha256,
              'industry_mapping_sha256': industry_mapping_sha256,
              'market_value_manifests_sha256': hashlib.sha256(
                  json.dumps(manifests, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    return build_industry_scale(rows, as_of=as_of, source=source)


def _valid_source(source: Any) -> bool:
    if not isinstance(source, dict):
        return False
    for key in ('scope_sha256', 'industry_mapping_sha256', 'market_value_manifests_sha256'):
        if not isinstance(source.get(key), str) or re.fullmatch('[0-9a-f]{64}', source[key]) is None:
            return False
    return source.get('universe') == UNIVERSE


def load_industry_scale(state_dir: Path, symbol: str, as_of: date) -> dict[str, Any]:
    result: dict[str, Any] = {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'unavailable',
                              'scale': None, 'threshold': None, 'min_peers': None, 'reason': '同行业市值排名缺失或校验失败'}
    path = Path(state_dir) / 'stock-leadership' / 'scale.json'
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get('version') != VERSION or payload.get('as_of') != as_of.isoformat():
            raise ValueError('市值排名版本或截点不符')
        threshold, min_peers = payload.get('threshold'), payload.get('min_peers')
        if type(threshold) is not int or threshold < 1 or type(min_peers) is not int or min_peers < 1:
            raise ValueError('市值排名阈值无效')
        if payload.get('universe') != UNIVERSE or not _valid_source(payload.get('source')):
            raise ValueError('市值排名范围或来源无效')
        symbols = payload.get('symbols')
        if not isinstance(symbols, dict) or len(symbols) < 2:
            raise ValueError('市值排名覆盖不足')
        for code, item in symbols.items():
            if (not isinstance(code, str) or re.fullmatch(r'\d{6}', code) is None or not isinstance(item, dict)
                    or not isinstance(item.get('industry'), str) or not item['industry'].strip()
                    or (item.get('rank') is not None and (type(item['rank']) is not int or item['rank'] < 1))
                    or type(item.get('peer_count')) is not int or item['peer_count'] < 0):
                raise ValueError('市值排名记录无效')
        if hashlib.sha256(_canonical(symbols)).hexdigest() != payload.get('source_sha256'):
            raise ValueError('市值排名摘要不符')
        result.update(status='current', scale=symbols.get(symbol), threshold=threshold, min_peers=min_peers,
                      file_sha256=hashlib.sha256(raw).hexdigest(), source=payload['source'],
                      reason='候选池内申万二级市值排名已绑定')
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    return result


def scale_passes(scale: Any, threshold: int, min_peers: int) -> bool:
    if not isinstance(scale, dict):
        return False
    rank, peers = scale.get('rank'), scale.get('peer_count')
    return type(rank) is int and type(peers) is int and 1 <= rank <= threshold and peers >= min_peers

"""候选池内同行业市值排名：只作规模地位，不冒充市场份额或全市场龙头。

v1 绑定旧的 626 家候选池；v2 绑定独立的同业范围产物（peer-universe），
要求成员全覆盖、市值日期一致，供新截点与单池更新复用。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

VERSION = 'industry-scale-v1'
VERSION_V2 = 'industry-scale-v2'
UNIVERSE = 'candidate_pool_626'
UNIVERSE_V2 = 'index_scope_union_v1'
THRESHOLD = 5
MIN_PEERS = 5
_SHA = re.compile(r'[0-9a-f]{64}')


def _amount(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _canonical(symbols: dict[str, Any]) -> bytes:
    return json.dumps(symbols, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def _sha(value: Any) -> str | None:
    return value if isinstance(value, str) and _SHA.fullmatch(value) else None


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _ranked(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """按申万二级和当前总市值排序；缺失市值保留 rank=None，不按 0 参与排名。"""
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
    return symbols


def build_industry_scale(rows: list[dict[str, Any]], *, as_of: date, source: dict[str, Any] | None = None,
                         threshold: int = THRESHOLD, min_peers: int = MIN_PEERS) -> dict[str, Any]:
    """v1：按申万二级和当前总市值排序；缺失市值保留 rank=None，不按 0 参与排名。"""
    if type(threshold) is not int or threshold < 1 or type(min_peers) is not int or min_peers < 1:
        raise ValueError('市值排名阈值无效')
    symbols = _ranked(rows)
    payload = {'version': VERSION, 'as_of': as_of.isoformat(), 'universe': UNIVERSE,
               'threshold': threshold, 'min_peers': min_peers,
               'source': dict(source or {}), 'source_sha256': hashlib.sha256(_canonical(symbols)).hexdigest(),
               'symbols': symbols}
    return payload


def build_peer_scale(rows: list[dict[str, Any]], *, as_of: date, peer_universe_sha256: str, company_count: int,
                     industry_mapping_sha256: str, scope_sha256: str, window_sha256: str,
                     threshold: int = THRESHOLD, min_peers: int = MIN_PEERS) -> dict[str, Any]:
    """v2：同业范围成员必须全覆盖且市值日期一致，否则拒绝生成。"""
    if type(threshold) is not int or threshold < 1 or type(min_peers) is not int or min_peers < 1:
        raise ValueError('市值排名阈值无效')
    for value, label in ((peer_universe_sha256, '同业范围'), (industry_mapping_sha256, '行业映射'),
                         (scope_sha256, '候选范围'), (window_sha256, '市场窗口')):
        if _sha(value) is None:
            raise ValueError(f'市值排名{label}摘要无效')
    if type(company_count) is not int or company_count != len(rows) or company_count < 2:
        raise ValueError('市值排名同业范围成员不完整')
    symbols_seen: set[str] = set()
    dates: set[str] = set()
    manifests: dict[str, str] = {}
    for row in rows:
        symbol = row.get('symbol')
        if not isinstance(symbol, str) or symbol in symbols_seen:
            raise ValueError('市值排名同业成员重复或无效')
        symbols_seen.add(symbol)
        if _amount(row.get('market_cap_yuan')) is None:
            raise ValueError('市值排名成员缺少当前总市值')
        day = _day(row.get('market_cap_date'))
        if day is None or day > as_of:
            raise ValueError('市值排名成员市值日期无效')
        dates.add(day.isoformat())
        manifest = _sha(row.get('market_value_manifest_sha256'))
        if manifest is None:
            raise ValueError('市值排名成员市值来源摘要无效')
        manifests[symbol] = manifest
    if len(dates) != 1:
        raise ValueError('市值排名成员市值日期不一致')
    symbols = _ranked(rows)
    payload = {'version': VERSION_V2, 'as_of': as_of.isoformat(), 'universe': UNIVERSE_V2,
               'threshold': threshold, 'min_peers': min_peers, 'company_count': company_count,
               'market_cap_date': next(iter(dates)),
               'peer_universe_sha256': peer_universe_sha256,
               'source': {'peer_universe_sha256': peer_universe_sha256, 'scope_sha256': scope_sha256,
                          'industry_mapping_sha256': industry_mapping_sha256,
                          'market_value_manifests_sha256': hashlib.sha256(
                              json.dumps(manifests, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                          'window_sha256': window_sha256},
               'source_sha256': hashlib.sha256(_canonical(symbols)).hexdigest(), 'symbols': symbols}
    return payload


def save_industry_scale(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')


def _state_rows(state_dir: Path, db: Path, as_of: date, symbols: list[str]) -> tuple[list[dict[str, Any]],
                                                                                    dict[str, str]]:
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
    return rows, manifests


def build_from_state(state_dir: Path, db: Path, as_of: date, symbols: list[str], *,
                     scope_sha256: str, industry_mapping_sha256: str) -> dict[str, Any]:
    """从同截点市值与行业来源生成排名；调用方负责把结果写入 scale.json。"""
    rows, manifests = _state_rows(state_dir, db, as_of, symbols)
    source = {'universe': UNIVERSE, 'scope_sha256': scope_sha256,
              'industry_mapping_sha256': industry_mapping_sha256,
              'market_value_manifests_sha256': hashlib.sha256(
                  json.dumps(manifests, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    return build_industry_scale(rows, as_of=as_of, source=source)


def build_peer_scale_from_state(state_dir: Path, db: Path, as_of: date, symbols: list[str], *,
                                peer_universe_sha256: str, scope_sha256: str,
                                industry_mapping_sha256: str, window_sha256: str,
                                threshold: int = THRESHOLD, min_peers: int = MIN_PEERS) -> dict[str, Any]:
    """从同截点市值来源生成 v2 排名；成员不齐或日期不一致时抛错。"""
    rows, _manifests = _state_rows(state_dir, db, as_of, symbols)
    return build_peer_scale(rows, as_of=as_of, peer_universe_sha256=peer_universe_sha256,
                            company_count=len(symbols), industry_mapping_sha256=industry_mapping_sha256,
                            scope_sha256=scope_sha256, window_sha256=window_sha256,
                            threshold=threshold, min_peers=min_peers)


def _valid_source(source: Any) -> bool:
    if not isinstance(source, dict):
        return False
    for key in ('scope_sha256', 'industry_mapping_sha256', 'market_value_manifests_sha256'):
        if _sha(source.get(key)) is None:
            return False
    return source.get('universe') == UNIVERSE


def _valid_v2(payload: dict[str, Any]) -> None:
    if payload.get('universe') != UNIVERSE_V2:
        raise ValueError('市值排名范围标识无效')
    source = payload.get('source')
    if not isinstance(source, dict):
        raise TypeError('市值排名来源无效')
    for key in ('peer_universe_sha256', 'scope_sha256', 'industry_mapping_sha256',
                'market_value_manifests_sha256', 'window_sha256'):
        if _sha(source.get(key)) is None:
            raise ValueError('市值排名来源摘要无效')
    if _sha(payload.get('peer_universe_sha256')) is None:
        raise ValueError('市值排名同业范围摘要无效')
    count = payload.get('company_count')
    if type(count) is not int or count < 2:
        raise ValueError('市值排名同业范围数量无效')
    cap_day = _day(payload.get('market_cap_date'))
    if cap_day is None or cap_day > date.fromisoformat(str(payload.get('as_of'))):
        raise ValueError('市值排名市值日期无效')
    symbols = payload.get('symbols')
    if not isinstance(symbols, dict) or len(symbols) != count:
        raise ValueError('市值排名成员数量不符')
    for code, item in symbols.items():
        if (not isinstance(code, str) or re.fullmatch(r'\d{6}', code) is None or not isinstance(item, dict)
                or not isinstance(item.get('industry'), str) or not item['industry'].strip()
                or (item.get('rank') is not None and (type(item['rank']) is not int or item['rank'] < 1))
                or type(item.get('peer_count')) is not int or item['peer_count'] < 0
                or item.get('market_cap_date') != payload['market_cap_date']
                or _sha(item.get('market_value_manifest_sha256')) is None
                or item.get('market_cap_yuan') is None):
            raise ValueError('市值排名成员记录无效')
    if hashlib.sha256(_canonical(symbols)).hexdigest() != payload.get('source_sha256'):
        raise ValueError('市值排名摘要不符')


def load_industry_scale(state_dir: Path, symbol: str, as_of: date) -> dict[str, Any]:
    result: dict[str, Any] = {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'unavailable',
                              'scale': None, 'threshold': None, 'min_peers': None, 'reason': '同行业市值排名缺失或校验失败'}
    path = Path(state_dir) / 'stock-leadership' / 'scale.json'
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError('市值排名结构无效')
        version = payload.get('version')
        if version == VERSION_V2:
            if payload.get('as_of') != as_of.isoformat():
                raise ValueError('市值排名截点不符')
            _valid_v2(payload)
        elif version == VERSION:
            if payload.get('as_of') != as_of.isoformat():
                raise ValueError('市值排名版本或截点不符')
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
        else:
            raise ValueError('市值排名版本无效')
        threshold, min_peers = payload.get('threshold'), payload.get('min_peers')
        if type(threshold) is not int or threshold < 1 or type(min_peers) is not int or min_peers < 1:
            raise ValueError('市值排名阈值无效')
        result.update(status='current', scale=payload['symbols'].get(symbol), threshold=threshold, min_peers=min_peers,
                      file_sha256=hashlib.sha256(raw).hexdigest(), source=payload['source'],
                      version=version, company_count=payload.get('company_count'),
                      peer_universe_sha256=payload.get('peer_universe_sha256'),
                      reason='候选池内申万二级市值排名已绑定')
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    return result


def scale_passes(scale: Any, threshold: int, min_peers: int) -> bool:
    if not isinstance(scale, dict):
        return False
    rank, peers = scale.get('rank'), scale.get('peer_count')
    return type(rank) is int and type(peers) is int and 1 <= rank <= threshold and peers >= min_peers

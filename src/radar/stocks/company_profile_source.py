"""只读官方名录，计算上市年限并保留身份与来源差异。"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from radar.stocks.admission import _day
from radar.stocks.market_value_registry import _bytes, _path, _reference
from radar.stocks.market_value_source import _scope
from radar.stocks.official_roster import _parse_source, _url
from radar.stocks.structured_collect import _json

logger = logging.getLogger(__name__)


@lru_cache(maxsize=3)
def _parsed(raw: bytes, source: str, parser: Callable[[bytes, str], dict[str, Any]]) -> dict[str, Any]:
    """仅缓存最多三份原始字节的纯解析；每次读取仍核验文件与摘要。"""
    return parser(raw, source)


def load_company_profile_source(state_dir: Path, symbol: str, as_of: date) -> dict[str, Any]:
    """缺失及冲突不放行；历史年限不伪装当前资格。"""
    if type(as_of) is not date:
        raise ValueError('评估日期无效')
    result: dict[str, Any] = {'symbol': symbol, 'evaluation_as_of': as_of.isoformat(), 'as_of': None,
                              'status': 'unavailable', 'manifest_sha256': None, 'name_match': None,
                              'roster_name': None, 'checks': [{'code': 'listing_age', 'outcome': 'pending_verification',
                                                              'reason': '上市名录来源缺失或校验失败'}]}
    try:
        root = Path(state_dir).absolute() / 'stock-company-profile'
        manifest_path = _path(root, 'manifest.json')
        raw = _bytes(manifest_path)
        result['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
        manifest = _json(raw)
        if not isinstance(manifest, dict) or manifest.get('version') != 'stock-company-profile-v1':
            raise ValueError('登记版本无效')
        target = _day(manifest.get('as_of'))
        if target is None or target > as_of:
            raise ValueError('登记日期无效')
        scope_path, scope_raw = _reference(root, manifest.get('scope'), need_size=True)
        scope = _json(scope_raw)
        _scope(scope, symbol, target)
        company = next(row for row in scope['companies'] if row['symbol'] == symbol)
        refs = manifest.get('rosters')
        if not isinstance(refs, list) or not refs:
            raise ValueError('名录缺失')
        originals = [(scope_path, scope_raw)]
        seen = set()
        matches = []
        provenance = []
        for ref in refs:
            if not isinstance(ref, dict):
                raise TypeError('名录引用无效')
            source = ref.get('source')
            if not isinstance(source, str) or source not in {'szse_a', 'sse_main', 'sse_star'} or source in seen:
                raise ValueError('名录来源无效或重复')
            seen.add(source)
            if ref.get('source_url') != _url(source):
                raise ValueError('名录端点不匹配')
            fetched = datetime.fromisoformat(ref.get('fetched_at', ''))
            if fetched.tzinfo is None or fetched.utcoffset() is None or fetched > datetime.now(UTC):
                raise ValueError('抓取时间无效')
            path, data = _reference(root, ref, need_size=True)
            parsed = _parsed(data, source, _parse_source)
            if parsed.get('complete') is not True:
                raise ValueError('名录不完整')
            for candidate in parsed['rows']:
                if candidate['symbol'] != symbol:
                    continue
                listed_day = _day(candidate.get('a_listed_on'))
                if listed_day is not None and listed_day > fetched.date():
                    raise ValueError('上市日期晚于名录抓取日期')
                matches.append(candidate)
            originals.append((path, data))
            provenance.append({key: ref[key] for key in ('source', 'source_url', 'sha256', 'bytes', 'fetched_at')})
        if len(matches) != 1:
            raise ValueError('代码缺失或重复')
        row = matches[0]
        listed = _day(row.get('a_listed_on'))
        if listed is None or listed > target:
            raise ValueError('上市日期无效')
        try:
            anniversary = listed.replace(year=listed.year + 3)
        except ValueError:
            anniversary = date(listed.year + 3, 2, 28)
        if _bytes(manifest_path) != raw or any(_bytes(path) != data for path, data in originals):
            raise ValueError('来源在读取期间变化')
        result.update(as_of=target.isoformat(), status='current' if target == as_of else 'historical',
                      name_match=row['name'] == company['name'], roster_name=row['name'],
                      source={'scope_sha256': manifest['scope']['sha256'], 'rosters': provenance},
                      checks=[{'code': 'listing_age', 'outcome': 'passed' if target >= anniversary else 'failed',
                               'reason': '按A股上市完整三周年计算；代码匹配不替代发行人身份核验',
                               'values': {'a_listed_on': listed.isoformat(), 'third_anniversary': anniversary.isoformat()}}])
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
        logger.debug('上市名录来源校验失败，保留待核验: %s', exc)
    return result

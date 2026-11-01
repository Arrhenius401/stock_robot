"""申万官方历史原件行业参考，不授予龙头资格。"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pandas as pd

from radar.stocks.admission import _day
from radar.stocks.market_value_registry import _bytes, _path, _reference
from radar.stocks.market_value_source import _scope
from radar.stocks.structured_collect import _json

logger = logging.getLogger(__name__)


def _time(entry: dict[str, Any]) -> None:
    if 'fetched_at' not in entry:
        raise ValueError('缺少时间依据')
    value = entry['fetched_at']
    if value is None:
        if entry.get('time_basis') != 'not_recorded':
            raise ValueError('未知时间须明确标注')
    else:
        parsed = datetime.fromisoformat(value)
        if (parsed.tzinfo is None or parsed.utcoffset() is None or parsed > datetime.now(UTC)
                or entry.get('time_basis') not in (None, 'recorded')):
            raise ValueError('抓取时间无效')


@lru_cache(maxsize=2)
def _classifications(history: bytes, taxonomy: bytes, cutoff: date) -> dict[str, list[dict[str, str]]]:
    frame = pd.read_excel(io.BytesIO(taxonomy), sheet_name=1, header=None, dtype=str).fillna('')
    definitions: dict[str, dict[str, str]] = {}
    first = second = ''
    for row in frame.itertuples(index=False, name=None):
        if len(row) < 8:
            raise ValueError('分类表列数不完整')
        a, b, third, code = map(str, row[4:8])
        if len(code) != 6 or not code.isdigit():
            continue
        if a:
            first, second = a, ''
        if b:
            second = b
        value = {'level1': first, 'level2': second, 'level3': third}
        if code in definitions:
            raise ValueError('分类代码重复')
        definitions[code] = value
    if len(definitions) < 490:
        raise ValueError('分类表不完整')
    table = pd.read_excel(io.BytesIO(history), sheet_name=0, dtype=str).fillna('')
    required = {'股票代码', '计入日期', '行业代码', '更新日期'}
    if not required.issubset(table.columns):
        raise ValueError('历史表字段不完整')
    candidates: dict[str, list[tuple[date, date, str]]] = {}
    for row in table.to_dict(orient='records'):
        symbol = str(row['股票代码']).zfill(6)
        start, updated = _day(str(row['计入日期'])[:10]), _day(str(row['更新日期'])[:10])
        if len(symbol) != 6 or not symbol.isdigit() or start is None or updated is None:
            raise ValueError('历史表身份或日期无效')
        if start <= cutoff and updated <= cutoff:
            candidates.setdefault(symbol, []).append((start, updated, str(row['行业代码'])))
    result: dict[str, list[dict[str, str]]] = {}
    for symbol, rows in candidates.items():
        latest_start = max(row[0] for row in rows)
        active = [row for row in rows if row[0] == latest_start]
        if len({row[2] for row in active}) != 1:
            continue
        start, updated, code = max(active)
        value = definitions.get(code)
        if value and all(value.values()):
            result[symbol] = [{**value, 'code': code, 'effective_date': start.isoformat(), 'update_date': updated.isoformat()}]
    return result


def _registered_load_industry_source(state_dir: Path, symbol: str, as_of: date, manifest_file: Path | None = None) -> dict[str, Any]:
    """严格复读登记及原始表格，冲突与损坏来源保持未知。"""
    if type(as_of) is not date:
        raise ValueError('评估日期无效')
    result: dict[str, Any] = {'symbol': symbol, 'evaluation_as_of': as_of.isoformat(), 'as_of': None,
                              'status': 'unavailable', 'industry': None, 'manifest_sha256': None,
                              'reason': '行业参考资料缺失或校验失败'}
    try:
        root = Path(state_dir).absolute() / 'stock-company-industry'
        if manifest_file is not None:
            root = manifest_file.parent
        manifest_path = _path(root, manifest_file.name if manifest_file is not None else 'manifest.json')
        raw = _bytes(manifest_path)
        result['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
        manifest = _json(raw)
        target = _day(manifest.get('as_of'))
        if manifest.get('version') != 'stock-company-industry-v1' or target is None or target > as_of:
            raise ValueError('登记版本或日期无效')
        _, scope_raw = _reference(root, manifest['scope'], need_size=True)
        _scope(_json(scope_raw), symbol, target)
        originals = []
        for key in ('history', 'taxonomy'):
            entry = manifest[key]
            _time(entry)
            url = urlsplit(entry.get('source_url', ''))
            if (url.scheme != 'https' or url.hostname != 'www.swsresearch.com' or url.username is not None
                    or url.password is not None or url.port not in (None,443)):
                raise ValueError('来源地址不匹配')
            _, data = _reference(root, entry, need_size=True)
            originals.append((entry, data))
        matches = _classifications(originals[0][1], originals[1][1], target).get(symbol, [])
        if len(matches) != 1:
            raise ValueError('行业缺失或冲突')
        if (_bytes(manifest_path) != raw
                or _reference(root, manifest['scope'], need_size=True)[1] != scope_raw
                or any(_reference(root, entry, need_size=True)[1] != data for entry, data in originals)):
            raise ValueError('读取期间来源变化')
        result.update(as_of=target.isoformat(), status='current' if target == as_of else 'historical', industry=dict(matches[0]),
                      source={'provider': 'swsresearch', 'classification': '申万2021官方原件历史分类参考',
                              'scope_sha256': manifest['scope']['sha256'],
                              'artifacts': [{key: entry.get(key) for key in ('sha256', 'bytes', 'source_url', 'fetched_at', 'time_basis')} for entry, _ in originals]},
                      reason='申万官方历史分类参考，不代表行业龙头核验；未证明截点当时已可得')
    except Exception as exc:  # noqa: BLE001 第三方表格解析隔离，坏文件不影响详情
        logger.debug('行业来源校验不可用：%s', type(exc).__name__)
    return result


def load_industry_source(state_dir: Path, symbol: str, as_of: date, *, db: Path | None = None) -> dict[str, Any]:
    """股票池直接查本地行业映射；不发起更新、不依赖在线资料任务。"""
    from data.industry_classifier import IndustryClassifier
    from utils.paths import industry_mapping_path

    if type(as_of) is not date:
        raise ValueError('评估日期无效')
    result: dict[str, Any] = {'symbol': symbol, 'evaluation_as_of': as_of.isoformat(), 'as_of': None,
        'status': 'unavailable', 'industry': None, 'manifest_sha256': None,
        'reason': '本地行业映射缺失或尚未核验'}
    try:
        path = industry_mapping_path()
        raw = _bytes(path)
        result['manifest_sha256'] = hashlib.sha256(raw).hexdigest()
        classification = IndustryClassifier(path).lookup(symbol)
        if not classification.is_verified:
            return result
        if _bytes(path) != raw:
            raise ValueError('本地行业映射读取期间变化')
        result.update(status='local_reference', industry={'level1': classification.sw_level1, 'level2': classification.sw_level2},
            source={'provider': 'local_industry_mapping', 'classification': '本地申万行业映射',
                    'sha256': result['manifest_sha256'], 'mapping_status': classification.mapping_status,
                    'time_basis': 'not_recorded'}, reason='使用本地已核验行业映射，不代表行业龙头核验')
    except (OSError, ValueError, TypeError, KeyError, csv.Error) as exc:
        logger.debug('本地行业映射不可用：%s', type(exc).__name__)
    return result

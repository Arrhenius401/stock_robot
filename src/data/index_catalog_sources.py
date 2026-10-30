"""官方指数目录的具名字段解析。"""
from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class CatalogEntry:
    identity: str
    code: str
    name: str
    provider: str
    market: str
    return_type: str
    category: str = ''
    series_code: str = ''
    pe: float | None = None
    pb: float | None = None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if isfinite(result) else None


def _unique(items: list[CatalogEntry]) -> list[CatalogEntry]:
    if len({item.identity for item in items}) != len(items):
        raise ValueError('指数目录身份重复')
    return items


def parse_hsi_catalog(payload: dict[str, Any]) -> list[CatalogEntry]:
    if str(payload.get('code')) != '0' or not isinstance(payload.get('data'), list):
        raise ValueError('恒生目录响应无效')
    versions = {'PI': 'price', 'TRI_Grs': 'gross_total_return', 'TRI_Net': 'net_total_return'}
    result: list[CatalogEntry] = []
    for row in payload['data']:
        if not isinstance(row, dict):
            raise TypeError('恒生目录行无效')
        if row.get('regionId') != 'hongkong':
            continue
        version = versions.get(str(row.get('indexType') or ''))
        code, name = str(row.get('indexCode') or '').strip(), str(row.get('indexName') or '').strip()
        if not code or not name or not version:
            raise ValueError('恒生目录身份或收益版本缺失')
        result.append(CatalogEntry(identity=f'hsi:{code}', code=code, name=name, provider='hsi', market='hk',
            return_type=version, category=str(row.get('categoryFragment') or ''),
            series_code=str(row.get('indexSeriesCode') or '')))
    return _unique(result)


def parse_cni_catalog(payload: dict[str, Any]) -> list[CatalogEntry]:
    data = payload.get('data')
    if payload.get('code') != 200 or not isinstance(data, dict) or not isinstance(data.get('rows'), list):
        raise ValueError('国证目录响应无效')
    rows = data['rows']
    if not isinstance(data.get('total'), int) or len(rows) != data['total']:
        raise ValueError('国证目录分页不完整')
    result: list[CatalogEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError('国证目录行无效')
        code, name = str(row.get('indexcode') or '').strip(), str(row.get('indexname') or '').strip()
        if not code or not name:
            raise ValueError('国证目录身份缺失')
        # 国证列表未提供可靠收益类型字段，保留未知，不以简称推断。
        result.append(CatalogEntry(identity=f'cni:{code}', code=code, name=name, provider='cni',
            market='unknown', return_type='unknown', category=str(row.get('indextype') or ''),
            pe=_number(row.get('peDynamic')), pb=_number(row.get('pb'))))
    return _unique(result)




def parse_csi_catalog(rows: list[dict[str, Any]], series: str) -> list[CatalogEntry]:
    """解析中证导出的具名字段；资产及市场未知时保守保留未知。"""
    if not rows or not series:
        raise ValueError('中证目录为空或缺少系列')
    result: list[CatalogEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError('中证目录行无效')
        code = str(row.get('指数代码') or '').strip()
        if code.isdigit():
            code = code.zfill(6)
        name = str(row.get('指数简称') or '').strip()
        if not code or not name:
            raise ValueError('中证目录身份缺失')
        asset = str(row.get('资产类别') or '').strip()
        if asset != '股票':
            continue
        result.append(CatalogEntry(identity=f'csi:{code}', code=code, name=name, provider='csi',
            market='unknown', return_type='unknown', category=str(row.get('指数类别') or ''),
            series_code=series))
    return _unique(result)


def save_catalog_snapshot(path, items: list[CatalogEntry]) -> None:
    """仅完整非空快照可以原子替换旧快照。"""
    import json
    import os
    import tempfile
    from dataclasses import asdict

    if not items:
        raise ValueError('拒绝保存空目录')
    _unique(items)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temp_path = file.name
            json.dump([asdict(item) for item in items], file, ensure_ascii=False)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            os.unlink(temp_path)


def load_catalog_snapshot(path) -> list[CatalogEntry]:
    import json

    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, list) or not data:
        raise ValueError('目录快照为空或无效')
    return _unique([CatalogEntry(**row) for row in data])


def parse_sw_catalog(rows: list[dict[str, Any]], expected_total: int, category: str) -> list[CatalogEntry]:
    """申万分页响应统一为记录后校验总数；分类由请求确定。"""
    if expected_total < 1 or len(rows) != expected_total or not category:
        raise ValueError('申万目录分页不完整')
    result: list[CatalogEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError('申万目录行无效')
        code = str(row.get('指数代码') or '').strip()
        name = str(row.get('指数名称') or '').strip()
        if not code or not name:
            raise ValueError('申万目录身份缺失')
        result.append(CatalogEntry(identity=f'sw:{code}', code=code, name=name, provider='sw',
            market='unknown', return_type='unknown', category=category))
    return _unique(result)



def fetch_hsi_catalog() -> list[CatalogEntry]:
    """从恒生公开元数据接口采集目录。"""
    import requests

    url = ('https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/'
           'v1/product-data/pub/indexes/metadata/v2')
    response = requests.get(url, params={'data': 'allIndexes', 'language': 'zh-cn'}, timeout=10)
    response.raise_for_status()
    return parse_hsi_catalog(response.json())


def fetch_cni_catalog() -> list[CatalogEntry]:
    """采集国证目录，并用服务端总数拒绝截断。"""
    import requests

    response = requests.get('https://www.cnindex.com.cn/index/indexList',
        params={'channelCode': '-1', 'rows': '2000', 'pageNum': '1'}, timeout=10)
    response.raise_for_status()
    return parse_cni_catalog(response.json())


def fetch_csi_catalog(series: str) -> list[CatalogEntry]:
    """分别导出中证系列（1）和上证系列（2）。"""
    from io import BytesIO

    import pandas as pd
    import requests

    if series not in ('1', '2'):
        raise ValueError('中证目录系列无效')
    payload = {
        'sorter': {'sortField': 'null', 'sortOrder': None},
        'pager': {'pageNum': 1, 'pageSize': 10},
        'indexFilter': {'indexSeries': [series]},
    }
    response = requests.post('https://www.csindex.com.cn/csindex-home/exportExcel/indexAll/CH',
                             json=payload, timeout=20)
    response.raise_for_status()
    if not response.content.startswith(b'PK'):
        raise ValueError('中证目录响应不是 XLSX')
    frame = pd.read_excel(BytesIO(response.content), dtype={'指数代码': str})
    expected = '中证系列指数' if series == '1' else '上证系列指数'
    if '指数系列' not in frame or not (frame['指数系列'] == expected).all():
        raise ValueError('中证目录系列不匹配')
    return parse_csi_catalog(frame.fillna('').to_dict('records'), series)


def sync_catalog(path, fetch, *, refresh: bool = False, ttl_seconds: int = 7 * 86400):
    """目录失败时保留旧快照，返回来源状态供调用方解释。"""
    import logging
    import time

    import requests

    old = None
    try:
        old = load_catalog_snapshot(path)
        age = time.time() - path.stat().st_mtime
        if not refresh and 0 <= age < ttl_seconds:
            return old, 'cache'
    except (OSError, ValueError, TypeError, KeyError) as error:
        logging.getLogger(__name__).debug('指数目录快照不可用：%s', error)
    try:
        result = fetch()
        save_catalog_snapshot(path, result)
        return result, 'live'
    except (requests.RequestException, OSError, ValueError, TypeError, KeyError) as error:
        logging.getLogger(__name__).warning('指数目录同步失败：%s', error)
        if old is not None:
            return old, 'stale'
        raise

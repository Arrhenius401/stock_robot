"""官方短名解析及版本隔离回归。"""
import pytest

from data.index_catalog_sources import (
    parse_hsi_catalog,
    save_catalog_aliases,
    save_catalog_snapshot,
)
from data.index_mapping import IndexMapping
from utils.symbols import normalize_index_query


@pytest.mark.parametrize('raw,expected', [('ｈｓｔｅｃｈ．ｈｋ','HSTECH'),('HSI:2083.00','HSI:02083.00'),('HSI:02083','HSI:02083.00'),('sh000300','000300'),('000300.SH','000300')])
def test_normalize(raw, expected):
    assert normalize_index_query(raw) == expected

@pytest.mark.parametrize('query,code', [('HSTECH.HK','02083.00'),('HSTECH TRI','12083.00'),('HSTECH-NTRI','22083.00'),('恒生科技','02083.00'),('HSI:2083.00','02083.00')])
def test_resolve(tmp_path, query, code):
    rows = [{'indexCode': c, 'indexName': n, 'indexShortName': a, 'indexType': t, 'regionId': 'hongkong', 'indexSeriesCode': 'hstech'} for c,n,a,t in [('02083.00','恒生科技指数','HSTECH','PI'),('12083.00','恒生科技指数总收益','HSTECH TRI','TRI_Grs'),('22083.00','恒生科技指数净收益','HSTECH NTRI','TRI_Net')]]
    items = parse_hsi_catalog({'code':'0','data':rows})
    path = tmp_path / 'index-catalog-hsi.json'
    save_catalog_snapshot(path, items)
    save_catalog_aliases(path, items)
    mapping = IndexMapping(snapshot_path=path)
    entry = mapping.resolve(query)
    assert entry is not None
    assert entry.symbol == 'HSI:' + code
    assert mapping.resolve('恒科') is None


def test_cross_family_never_prefers_price(tmp_path):
    from data.index_catalog_sources import CatalogEntry
    items = [CatalogEntry('hsi:00011.00', '00011.00', '样本指数', 'hsi', 'hk', 'price', series_code='one'),
             CatalogEntry('hsi:10012.00', '10012.00', '样本指数总收益', 'hsi', 'hk', 'gross_total_return', series_code='two')]
    path = tmp_path / 'index-catalog-hsi.json'
    save_catalog_snapshot(path, items)
    save_catalog_aliases(path, items)
    mapping = IndexMapping(snapshot_path=path)
    assert len(mapping.resolve_candidates('样本')) == 2


def test_csi_catalog_series_is_not_a_return_version_family(tmp_path):
    from data.index_catalog_sources import CatalogEntry
    items = [CatalogEntry('csi:999111', '999111', '样本一指数', 'csi', 'unknown', 'unknown', series_code='1'),
             CatalogEntry('csi:999222', '999222', '样本二指数', 'csi', 'unknown', 'unknown', series_code='1')]
    path = tmp_path / 'index-catalog-csi1.json'
    save_catalog_snapshot(path, items)
    matches = IndexMapping(snapshot_path=path).resolve_candidates('样本一')
    assert [entry.symbol for entry in matches] == ['CSI:999111']

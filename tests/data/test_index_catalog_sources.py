"""官方目录解析回归：2026-10-29 恒生和国证响应字段摘录。"""
import pytest

from data.index_catalog_sources import parse_cni_catalog, parse_hsi_catalog


def test_hsi_keeps_return_versions_and_excludes_other_regions():
    data = [
        {'indexCode': '00001.00', 'indexName': '恒生指数', 'indexSeriesCode': 'hsi', 'indexType': 'PI', 'regionId': 'hongkong', 'categoryFragment': 'market'},
        {'indexCode': '10068.00', 'indexName': '恒生指数总收益', 'indexSeriesCode': 'hsi', 'indexType': 'TRI_Grs', 'regionId': 'hongkong', 'categoryFragment': 'market'},
        {'indexCode': '00003.00', 'indexName': '内地指数', 'indexSeriesCode': 'c', 'indexType': 'PI', 'regionId': 'mainland', 'categoryFragment': 'market'},
    ]
    items = parse_hsi_catalog({'code': '0', 'data': data})
    assert [(x.identity, x.return_type) for x in items] == [('hsi:00001.00', 'price'), ('hsi:10068.00', 'gross_total_return')]


def test_cni_rejects_incomplete_and_uses_named_fields():
    row = {'indexcode': '399001', 'indexname': '深证成指', 'indextype': '100', 'pb': 2.28, 'peDynamic': 20.6237}
    with pytest.raises(ValueError, match='分页'):
        parse_cni_catalog({'code': 200, 'data': {'total': 2, 'rows': [row]}})
    entry = parse_cni_catalog({'code': 200, 'data': {'total': 1, 'rows': [row]}})[0]
    assert entry.identity == 'cni:399001'
    assert (entry.pe, entry.pb) == (20.6237, 2.28)


def test_hsi_rejects_duplicate_identity():
    row = {'indexCode': '00001.00', 'indexName': '恒生指数', 'indexType': 'PI', 'regionId': 'hongkong'}
    with pytest.raises(ValueError, match='重复'):
        parse_hsi_catalog({'code': '0', 'data': [row, row]})


def test_csi_keeps_only_stock_assets_and_does_not_guess_region():
    from data.index_catalog_sources import parse_csi_catalog

    rows = [{'指数代码': '000300', '指数简称': '沪深300', '资产类别': '股票'},
            {'指数代码': '000012', '指数简称': '国债指数', '资产类别': '债券'}]
    items = parse_csi_catalog(rows, '1')
    assert len(items) == 1
    assert (items[0].identity, items[0].market) == ('csi:000300', 'unknown')


def test_catalog_snapshot_rejects_empty_without_replacing_previous(tmp_path):
    from data.index_catalog_sources import load_catalog_snapshot, save_catalog_snapshot

    records = parse_cni_catalog({'code': 200, 'data': {'total': 1, 'rows': [
        {'indexcode': '399001', 'indexname': '深证成指'},
    ]}})
    path = tmp_path / 'catalog.json'
    save_catalog_snapshot(path, records)
    with pytest.raises(ValueError):
        save_catalog_snapshot(path, [])
    assert load_catalog_snapshot(path) == records


def test_sw_requires_complete_pages():
    from data.index_catalog_sources import parse_sw_catalog

    rows = [{'指数代码': '801010', '指数名称': '农林牧渔'}]
    with pytest.raises(ValueError, match='分页'):
        parse_sw_catalog(rows, 2, '一级行业')
    assert parse_sw_catalog(rows, 1, '一级行业')[0].identity == 'sw:801010'


def test_sync_uses_fresh_cache_and_preserves_old_on_failure(tmp_path):
    from data.index_catalog_sources import sync_catalog

    calls = []
    row = {'indexcode': '399001', 'indexname': '深证成指'}

    def fetch():
        calls.append(1)
        return parse_cni_catalog({'code': 200, 'data': {'total': 1, 'rows': [row]}})

    path = tmp_path / 'cni.json'
    first, state = sync_catalog(path, fetch)
    assert state == 'live'
    assert sync_catalog(path, fetch)[1] == 'cache'
    assert len(calls) == 1

    def fail():
        raise ValueError('源不可用')

    stale, state = sync_catalog(path, fail, refresh=True)
    assert state == 'stale'
    assert stale == first


def test_mapping_preserves_hsi_versions_and_legacy_alias(tmp_path):
    from data.index_catalog_sources import save_catalog_snapshot
    from data.index_mapping import IndexMapping

    entries = parse_hsi_catalog({'code': '0', 'data': [
        {'indexCode': '00001.00', 'indexName': '恒生指数', 'indexType': 'PI', 'regionId': 'hongkong'},
        {'indexCode': '10068.00', 'indexName': '恒生指数', 'indexType': 'TRI_Grs', 'regionId': 'hongkong'},
    ]})
    path = tmp_path / 'hsi.json'
    save_catalog_snapshot(path, entries)
    mapping = IndexMapping(snapshot_path=path)
    entry = mapping.resolve('HSI')
    assert entry is not None and entry.symbol == 'HSI'
    entry = mapping.resolve('hsi:00001.00')
    assert entry is not None and entry.price_symbol == '00001.00'
    entry = mapping.resolve('HSI:10068.00')
    assert entry is not None and entry.return_type == 'gross_total_return'
    with pytest.raises(ValueError, match='多个指数'):
        mapping.resolve('恒生指数')


def test_sync_failure_does_not_write_partial_results(tmp_path):
    from data.index_catalog_sources import sync_catalog

    path = tmp_path / 'catalog.json'
    with pytest.raises(ValueError):
        sync_catalog(path, list, refresh=True)
    assert not path.exists()


def test_known_official_a_share_identity_reuses_verified_legacy_mapping(tmp_path):
    from data.index_catalog_sources import CatalogEntry, save_catalog_snapshot
    from data.index_mapping import IndexMapping

    path = tmp_path / 'csi.json'
    save_catalog_snapshot(path, [CatalogEntry(identity='csi:000300', code='000300', name='沪深300',
        provider='csi', market='unknown', return_type='unknown')])
    mapping = IndexMapping(snapshot_path=path)
    resolved = mapping.resolve('CSI:000300')
    assert resolved is not None
    assert (resolved.symbol, resolved.market, resolved.identity) == ('000300', 'a-shares', 'csi:000300')


def test_conflicting_official_name_stays_unknown(tmp_path):
    from data.index_catalog_sources import CatalogEntry, save_catalog_snapshot
    from data.index_mapping import IndexMapping

    path = tmp_path / 'csi.json'
    save_catalog_snapshot(path, [CatalogEntry(identity='csi:000300', code='000300', name='另一指数',
        provider='csi', market='unknown', return_type='unknown')])
    resolved = IndexMapping(snapshot_path=path).resolve('CSI:000300')
    assert resolved is not None and resolved.market == 'unknown'


def test_csi_snapshot_does_not_make_legacy_name_ambiguous(tmp_path):
    from data.index_catalog_sources import CatalogEntry, save_catalog_snapshot
    from data.index_mapping import IndexMapping

    path = tmp_path / 'csi.json'
    save_catalog_snapshot(path, [CatalogEntry(identity='csi:000300', code='000300', name='沪深300',
        provider='csi', market='unknown', return_type='unknown')])
    mapping = IndexMapping(snapshot_path=path)
    entry = mapping.resolve('沪深300')
    assert entry is not None and entry.symbol == '000300'
    entry = mapping.resolve('CSI:000300')
    assert entry is not None and entry.symbol == '000300'


def test_hsi_price_alias_is_not_duplicate_but_total_return_is_distinct(tmp_path):
    from data.index_catalog_sources import save_catalog_snapshot
    from data.index_mapping import IndexMapping

    price = {'indexCode': '00001.00', 'indexName': '恒生指数', 'indexType': 'PI', 'regionId': 'hongkong'}
    total = {'indexCode': '10068.00', 'indexName': '恒生指数', 'indexType': 'TRI_Grs', 'regionId': 'hongkong'}
    path = tmp_path / 'hsi.json'
    save_catalog_snapshot(path, parse_hsi_catalog({'code': '0', 'data': [price]}))
    entry = IndexMapping(snapshot_path=path).resolve('恒生指数')
    assert entry is not None and entry.symbol == 'HSI'
    save_catalog_snapshot(path, parse_hsi_catalog({'code': '0', 'data': [price, total]}))
    with pytest.raises(ValueError, match='多个指数'):
        IndexMapping(snapshot_path=path).resolve('恒生指数')

"""恒生官方短名侧车及旧快照兼容契约。"""
import json

from data.index_catalog_sources import parse_hsi_catalog, save_catalog_snapshot


def test_short_name_is_data_and_main_snapshot_keeps_old_schema(tmp_path):
    rows = [
        {'indexCode': code, 'indexName': '测试指数', 'indexType': version,
         'regionId': 'hongkong', 'indexShortName': short}
        for code, version, short in (
            ('02083.00', 'PI', 'HSTECH'),
            ('12083.00', 'TRI_Grs', 'HSTECH TRI'),
            ('22083.00', 'TRI_Net', 'HSTECH NTRI'),
        )
    ]
    items = parse_hsi_catalog({'code': '0', 'data': rows})
    assert [item.aliases for item in items] == [('HSTECH',), ('HSTECH TRI',), ('HSTECH NTRI',)]
    path = tmp_path / 'index-catalog-hsi.json'
    save_catalog_snapshot(path, items)
    assert all('aliases' not in row for row in json.loads(path.read_text(encoding='utf-8')))


def test_invalid_short_names_do_not_become_invented_aliases():
    for short in (None, '', '  ', 123, ['HSTECH']):
        items = parse_hsi_catalog({'code': '0', 'data': [{
            'indexCode': '02083.00', 'indexName': '测试指数', 'indexType': 'PI',
            'regionId': 'hongkong', 'indexShortName': short,
        }]})
        assert items[0].aliases == ()


def test_sidecar_roundtrip_and_identity_validation(tmp_path):
    from data.index_catalog_sources import load_catalog_aliases, save_catalog_aliases

    items = parse_hsi_catalog({'code': '0', 'data': [{
        'indexCode': '02083.00', 'indexName': '测试指数', 'indexType': 'PI',
        'regionId': 'hongkong', 'indexShortName': 'HSTECH',
    }]})
    path = tmp_path / 'index-catalog-hsi.json'
    assert load_catalog_aliases(path, items) is None
    save_catalog_aliases(path, items)
    assert load_catalog_aliases(path, items) == {'hsi:02083.00': ('HSTECH',)}
    sidecar = path.with_suffix('.aliases.json')
    sidecar.write_text('{"other": []}', encoding='utf-8')
    assert load_catalog_aliases(path, items) is None
    sidecar.write_text('broken', encoding='utf-8')
    assert load_catalog_aliases(path, items) is None


def test_mapping_exposes_old_snapshot_and_complete_alias_status(tmp_path):
    from data.index_catalog_sources import save_catalog_aliases
    from data.index_mapping import IndexMapping

    items = parse_hsi_catalog({'code': '0', 'data': [{
        'indexCode': '02083.00', 'indexName': '测试指数', 'indexType': 'PI',
        'regionId': 'hongkong', 'indexShortName': 'HSTECH',
    }]})
    path = tmp_path / 'index-catalog-hsi.json'
    save_catalog_snapshot(path, items)
    mapping = IndexMapping(snapshot_path=path)
    assert mapping.catalog_status['hsi_loaded'] is True
    assert mapping.catalog_status['hsi_aliases_loaded'] is False
    save_catalog_aliases(path, items)
    mapping = IndexMapping(snapshot_path=path)
    assert mapping.catalog_status['hsi_aliases_loaded'] is True
    entry = mapping.lookup('HSI:02083.00')
    assert entry is not None
    assert entry.aliases == ('HSTECH',)


def test_v040_loader_reads_new_main_snapshot(tmp_path):
    import importlib.util
    import subprocess
    import sys

    source = subprocess.check_output(['git', 'show', 'v0.4.0:src/data/index_catalog_sources.py'], text=True, encoding='utf-8')
    old_path = tmp_path / 'v040_index_catalog_sources.py'
    old_path.write_text(source, encoding='utf-8')
    spec = importlib.util.spec_from_file_location('_catalog_v040_compat', old_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    try:
        spec.loader.exec_module(module)
        items = parse_hsi_catalog({'code': '0', 'data': [{
            'indexCode': '02083.00', 'indexName': '测试指数', 'indexType': 'PI',
            'regionId': 'hongkong', 'indexShortName': 'HSTECH',
        }]})
        path = tmp_path / 'index-catalog-hsi.json'
        save_catalog_snapshot(path, items)
        loaded = module.load_catalog_snapshot(path)
        assert loaded[0].identity == 'hsi:02083.00'
    finally:
        del sys.modules[module.__name__]


def test_sidecar_write_failure_is_stale_and_not_loaded(tmp_path, monkeypatch):
    import data.index_catalog_sources as sources
    from data.index_mapping import IndexMapping

    items = parse_hsi_catalog({'code': '0', 'data': [{
        'indexCode': '02083.00', 'indexName': '测试指数', 'indexType': 'PI',
        'regionId': 'hongkong', 'indexShortName': 'HSTECH',
    }]})
    path = tmp_path / 'index-catalog-hsi.json'
    sources.sync_catalog(path, lambda: items, refresh=True)
    old_main = path.read_bytes()
    old_sidecar = path.with_suffix('.aliases.json').read_bytes()
    original_writer = sources.save_catalog_aliases
    def fail(*args):
        raise OSError('侧车写入失败')
    monkeypatch.setattr(sources, 'save_catalog_aliases', fail)
    from dataclasses import replace
    changed = [replace(items[0], name='更新后的指数名称', aliases=('NEWNAME',))]
    assert sources.sync_catalog(path, lambda: changed, refresh=True)[1] == 'stale'
    assert path.read_bytes() == old_main
    assert path.with_suffix('.aliases.json').read_bytes() == old_sidecar
    assert IndexMapping(snapshot_path=path).catalog_status['hsi_aliases_loaded'] is False
    monkeypatch.setattr(sources, 'save_catalog_aliases', original_writer)
    assert sources.sync_catalog(path, lambda: changed, refresh=True)[1] == 'live'
    assert IndexMapping(snapshot_path=path).catalog_status['hsi_aliases_loaded'] is True

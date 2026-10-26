"""五组能力常量和遗留清单的基本事实。"""
from core.collection_contract import LEGACY_FETCH_SITES, MIGRATED_CAPABILITIES


def test_migrated_capabilities_are_the_five_expected_pairs():
    assert MIGRATED_CAPABILITIES == frozenset({
        ("a-shares", "financial"),
        ("hk", "financial"),
        ("a-shares", "price"),
        ("hk", "price"),
        ("a-shares", "news"),
    })


def test_legacy_fetch_sites_declare_current_migration_ledger():
    paths = {site.path for site in LEGACY_FETCH_SITES}
    assert {
        "src/data/base.py",
        "src/core/pipeline.py",
        "src/index/collector.py",
        "src/backtest/data.py",
    } <= paths
    assert all(site.phase for site in LEGACY_FETCH_SITES)

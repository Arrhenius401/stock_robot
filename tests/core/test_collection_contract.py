"""五组能力常量和遗留清单的基本事实。"""
import pytest

from core.collection_contract import LEGACY_FETCH_SITES, MIGRATED_CAPABILITIES
from core.pipeline import Pipeline
from core.registry import Registry
from data.base import DataSource
from utils.config import Config


class BadSource(DataSource):
    def supports(self, market, data_type):
        return True

    def fetch(self, symbol, **kwargs):
        return []


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


def test_pipeline_rejects_source_without_explicit_fetch_result(tmp_path):
    registry = Registry()
    registry.register_data_source(BadSource())
    with pytest.raises(ValueError, match="fetch_result"):
        Pipeline(registry, Config(config_dir=tmp_path), llm_enabled=False)


def test_late_registered_bad_source_returns_contract_error_without_cache(tmp_path, mocker):
    mocker.patch("core.pipeline.time.sleep")
    pipeline = Pipeline(Registry(), Config(config_dir=tmp_path), llm_enabled=False)
    pipeline._registry.register_data_source(BadSource())
    ctx = pipeline.collect("000001", "测试", data_types=["price"])
    record = ctx.collection_records["price"]
    assert record.error_kind == "contract_error"
    assert record.result_state == "failed"
    assert pipeline._cache.get_entry("price", "000001", "last_attempt") is None

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


def test_legacy_fetch_sites_contain_only_compatibility_bridge():
    assert {site.path for site in LEGACY_FETCH_SITES} == {"src/data/base.py"}
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


def test_dedicated_capabilities_and_entrypoint_ledger():
    from core.collection_contract import (
        ALLOWED_FETCH_RESULT_CALLS,
        CONTRACT_CAPABILITIES,
        DEDICATED_CAPABILITIES,
        GOLD_CAPABILITIES,
    )
    from data.collection_policies import DEDICATED_POLICIES

    assert ("src/backtest/data.py", "HistoricalPriceProvider.fetch_stock_result") in ALLOWED_FETCH_RESULT_CALLS
    assert DEDICATED_CAPABILITIES == {
        ("hk", "news"), ("a-shares", "valuation"), ("hk", "valuation"),
        ("a-shares", "industry"), ("hk", "industry"),
    }
    assert set(DEDICATED_POLICIES) == DEDICATED_CAPABILITIES
    assert CONTRACT_CAPABILITIES == MIGRATED_CAPABILITIES | DEDICATED_CAPABILITIES | GOLD_CAPABILITIES
    assert GOLD_CAPABILITIES == {("gold", key) for key in (
        "domestic", "international", "real_yield", "dollar", "fx", "china_reserves", "holdings", "global_reserves",
    )}
    assert not any(site.path == "src/core/pipeline.py" for site in LEGACY_FETCH_SITES)
    assert {
        ("src/core/resilient_collection.py", "ResilientCollector._collect"),
        ("src/core/pipeline.py", "Pipeline.collect.fetch_one"),
        ("src/core/pipeline.py", "Pipeline._collect_hk_news"),
        ("src/gold/data.py", "GoldCollector._request"),
    } <= ALLOWED_FETCH_RESULT_CALLS


class BadHKNewsSource(DataSource):
    def supports(self, market, data_type):
        return (market, data_type) == ("hk", "news")

    def fetch(self, symbol, **kwargs):
        raise AssertionError("不能调用遗留入口")


def test_pipeline_rejects_dedicated_source_without_explicit_fetch_result(tmp_path):
    registry = Registry()
    registry.register_data_source(BadHKNewsSource())
    with pytest.raises(ValueError, match="fetch_result"):
        Pipeline(registry, Config(config_dir=tmp_path), llm_enabled=False)


def test_late_dedicated_bad_source_does_not_write_cache(tmp_path, mocker):
    mocker.patch("core.pipeline.time.sleep")
    pipeline = Pipeline(Registry(), Config(config_dir=tmp_path), llm_enabled=False)
    pipeline._registry.register_data_source(BadHKNewsSource())
    ctx = pipeline.collect("00700.HK", "腾讯", market="hk", data_types=["news"])
    record = ctx.collection_records["news"]
    assert record.error_kind == "contract_error"
    assert record.result_state == "failed"
    for key in ("latest", "last_complete", "last_attempt"):
        assert pipeline._cache.get_entry("news", "00700.HK", key) is None


def test_inconsistent_diagnostic_cannot_replace_valuation_cache(tmp_path, mocker):
    from datetime import datetime

    from data.base import CollectionResult
    from data.schemas import DataCollectionRecord, ValuationData
    from tests.support import FetchResultMixin

    class Source(FetchResultMixin, DataSource):
        def supports(self, market, data_type):
            return (market, data_type) == ("a-shares", "valuation")

        def fetch(self, symbol, **kwargs):
            return [ValuationData(symbol=symbol, date=datetime.now().astimezone().date(), pe_ttm=20)]

        def fetch_result(self, symbol, **kwargs):
            return CollectionResult(self.fetch(symbol), DataCollectionRecord(
                result_state="complete", cache_state="degraded"))

    mocker.patch("core.pipeline.time.sleep")
    registry = Registry()
    registry.register_data_source(Source())
    pipeline = Pipeline(registry, Config(config_dir=tmp_path), llm_enabled=False)
    original = ValuationData(symbol="000001", date=datetime.now().astimezone().date(), pe_ttm=10)
    pipeline._set_cache("000001", "valuation", [original])
    ctx = pipeline.collect("000001", "测试", data_types=["valuation"], refresh_cache=True)
    record = ctx.collection_records["valuation"]
    assert record.error_kind == "contract_error"
    assert record.cache_status == "live"
    cached = pipeline._get_cached("000001", "valuation")
    assert cached is not None and cached[0].pe_ttm == 10


def test_inconsistent_record_cannot_be_written(tmp_path):
    from datetime import datetime

    from data.schemas import DataCollectionRecord, ValuationData

    pipeline = Pipeline(Registry(), Config(config_dir=tmp_path), llm_enabled=False)
    row = ValuationData(symbol="000001", date=datetime.now().astimezone().date(), pe_ttm=10)
    record = DataCollectionRecord(result_state="complete", cache_state="degraded")
    pipeline._set_cache("000001", "valuation", [row], record)
    assert pipeline._cache.get_entry("valuation", "000001", "latest") is None
    pipeline._store_cache("000001", "valuation", [row], record)
    assert pipeline._cache.get_entry("valuation", "000001", "latest") is None

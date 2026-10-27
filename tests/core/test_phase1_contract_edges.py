"""Phase 1 专用路径的结果契约边界。"""
from datetime import datetime, timedelta

import pytest

from core.pipeline import Pipeline
from core.registry import Registry
from data.base import CollectionResult, DataSource
from data.hk_news import HK_TZ
from data.schemas import DataCollectionRecord, HKNewsSnapshot, NewsData, ValuationData
from utils.config import Config


class ResultSource(DataSource):
    def __init__(self, market, kind, result):
        self.market, self.kind, self.result = market, kind, result

    def supports(self, market, data_type):
        return (market, data_type) == (self.market, self.kind)

    def fetch(self, symbol, **kwargs):
        raise AssertionError("必须使用 fetch_result")

    def fetch_result(self, symbol, **kwargs):
        return self.result


def make_pipeline(tmp_path, mocker, market, kind, result):
    mocker.patch("core.pipeline.time.sleep")
    registry = Registry()
    registry.register_data_source(ResultSource(market, kind, result))
    return Pipeline(registry, Config(config_dir=tmp_path), llm_enabled=False)


@pytest.mark.parametrize("row", [
    ValuationData(symbol="000001", date=datetime.now(HK_TZ).date(), pe_ttm=float("nan")),
    ValuationData(symbol="000002", date=datetime.now(HK_TZ).date(), pe_ttm=10),
])
def test_failed_a_valuation_does_not_write_cache(tmp_path, mocker, row):
    pipe = make_pipeline(tmp_path, mocker, "a-shares", "valuation", CollectionResult([row]))
    ctx = pipe.collect("000001", "测试", data_types=["valuation"])
    assert ctx.collection_records["valuation"].result_state == "failed"
    assert pipe._cache.get_stale("valuation", "000001", "latest") is None


@pytest.mark.parametrize("row", [
    ValuationData(symbol="000001", date=datetime.now(HK_TZ).date(), pe_ttm=float("nan")),
    ValuationData(symbol="000002", date=datetime.now(HK_TZ).date(), pe_ttm=10),
])
def test_invalid_valuation_retry_waits_one_second(tmp_path, mocker, row):
    pipe = make_pipeline(tmp_path, mocker, "a-shares", "valuation", CollectionResult([row]))
    sleep = mocker.patch("core.pipeline.time.sleep")
    ctx = pipe.collect("000001", "测试", data_types=["valuation"])
    assert ctx.collection_records["valuation"].result_state == "failed"
    assert sleep.call_args_list.count(mocker.call(1)) == 1


@pytest.mark.parametrize("invalid", ["wrong_symbol", "future_date"])
def test_failed_hk_news_preserves_old_snapshot(tmp_path, mocker, invalid):
    now = datetime.now(HK_TZ)
    old = NewsData(symbol="00700.HK", date=now.date(), hk_news=HKNewsSnapshot(
        fetched_at=now - timedelta(hours=2), queries=["00700"]))
    row = NewsData(symbol="00005.HK" if invalid == "wrong_symbol" else "00700.HK",
                   date=now.date() + timedelta(days=1) if invalid == "future_date" else now.date(),
                   hk_news=HKNewsSnapshot(fetched_at=now, queries=["00700"]))
    pipe = make_pipeline(tmp_path, mocker, "hk", "news", CollectionResult([row]))
    pipe._set_cache("00700.HK", "news", [old])
    ctx = pipe.collect("00700.HK", "测试", market="hk", data_types=["news"], refresh_cache=True)
    assert ctx.news_data is not None and ctx.news_data.hk_news == old.hk_news
    assert ctx.collection_records["news"].cache_status == "stale"
    cached = pipe._get_stale_cached("00700.HK", "news")
    assert cached is not None and cached[0].hk_news == old.hk_news


def test_failed_hk_valuation_not_masked_by_old_component_merge(tmp_path, mocker):
    now = datetime.now(HK_TZ).date()
    old = ValuationData(symbol="00700.HK", date=now, pb=1, ps_ttm=2, ps_as_of=now,
                        ps_source="东方财富港股估值对比")
    new = ValuationData(symbol=old.symbol, date=now, pb=2, cache_degraded=True,
                        failed_components=["comparison"])
    pipe = make_pipeline(tmp_path, mocker, "hk", "valuation", CollectionResult([new, new]))
    pipe._set_cache(old.symbol, "valuation", [old])
    ctx = pipe.collect(old.symbol, "测试", market="hk", data_types=["valuation"], refresh_cache=True)
    assert ctx.collection_records["valuation"].result_state != "partial"
    cached = pipe._get_stale_cached(old.symbol, "valuation")
    assert cached is not None and cached[0].pb == 1


def test_hk_valuation_can_fill_missing_facts_from_old_ps(tmp_path, mocker):
    now = datetime.now(HK_TZ).date()
    old = ValuationData(symbol="00700.HK", date=now, ps_ttm=2, ps_as_of=now,
                        ps_source="东方财富港股估值对比")
    new = ValuationData(symbol=old.symbol, date=now, cache_degraded=True,
                        failed_components=["comparison"])
    pipe = make_pipeline(tmp_path, mocker, "hk", "valuation", CollectionResult([new]))
    pipe._set_cache(old.symbol, "valuation", [old])
    ctx = pipe.collect(old.symbol, "测试", market="hk", data_types=["valuation"], refresh_cache=True)
    assert ctx.valuation_data is not None and ctx.valuation_data.ps_ttm == 2
    assert ctx.collection_records["valuation"].result_state == "partial"


def test_failed_diagnostic_has_no_collected_time(tmp_path, mocker):
    diagnostic = DataCollectionRecord(source="来源", collected_at=datetime.now(HK_TZ),
                                      error_kind="timeout", error_message="请求超时")
    pipe = make_pipeline(tmp_path, mocker, "hk", "valuation", CollectionResult([], diagnostic))
    ctx = pipe.collect("00700.HK", "测试", market="hk", data_types=["valuation"])
    record = ctx.collection_records["valuation"]
    assert record.result_state == "failed"
    assert record.error_kind == "timeout"
    assert record.collected_at is None

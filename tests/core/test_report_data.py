from datetime import date, datetime, timedelta

import pytest

from analysis.technical import TechnicalAnalyzer
from core.pipeline import Pipeline
from core.registry import Registry
from data.cache import CacheManager
from data.enrichers.price_enricher import PriceEnricher
from data.schemas import AnalysisContext, AnalysisResult, PriceData
from utils.config import Config


def prices(count):
    return [
        PriceData(
            symbol="000001",
            trade_date=date(2026, 1, 1) + timedelta(days=i),
            open=10,
            high=11,
            low=9,
            close=10,
            volume=100,
        )
        for i in range(count)
    ]


@pytest.mark.parametrize(
    ("count", "status"),
    [
        (0, "unavailable"),
        (19, "unavailable"),
        (20, "partial"),
        (59, "partial"),
        (60, "ok"),
    ],
)
def test_technical_sample_status(count, status):
    ctx = PriceEnricher().enrich(
        AnalysisContext(symbol="000001", name="测试", price_data=prices(count))
    )
    assert TechnicalAnalyzer().analyze(ctx).status == status


def test_short_price_cached(tmp_path):
    pipe = Pipeline(Registry(), Config(config_dir=tmp_path), llm_enabled=False)
    pipe._set_cache("000001", "price", prices(19))
    assert len(pipe._get_cached("000001", "price")) == 19


def test_invalid_ohlc_not_cached(tmp_path):
    pipe = Pipeline(Registry(), Config(config_dir=tmp_path), llm_enabled=False)
    data = prices(60)
    data[0].high = 8
    pipe._set_cache("000001", "price", data)
    assert pipe._get_cached("000001", "price") is None


def test_no_scored_data_skips_commentary(tmp_path):
    pipe = Pipeline(Registry(), Config(config_dir=tmp_path), llm_enabled=True)
    assert (
        pipe._generate_commentary(
            "000001",
            "测试",
            [AnalysisResult(dimension="financial", status="unavailable", summary="")],
        )
        == {}
    )


def test_cache_metadata_lifecycle(tmp_path):
    from data.schemas import DataCollectionRecord

    cache = CacheManager(tmp_path / "cache.db")
    record = DataCollectionRecord(
        source="腾讯", collected_at=datetime.now().astimezone(), cache_status="live"
    )
    cache.put("price", "000001", "latest", "[]", metadata=record.model_dump_json())
    assert cache.get_metadata("price", "000001", "latest") == record.model_dump_json()
    cache.put("price", "000001", "latest", "[]")
    assert cache.get_metadata("price", "000001", "latest") is None
    cache.put("price", "000001", "latest", "[]", metadata=record.model_dump_json())
    cache.invalidate_symbol("000001")
    assert cache.get_metadata("price", "000001", "latest") is None


@pytest.mark.parametrize(
    "field,value", [("close", float("inf")), ("low", -1), ("trade_date", "broken")]
)
def test_mutated_invalid_price_rejected(tmp_path, field, value):
    pipe = Pipeline(Registry(), Config(config_dir=tmp_path), llm_enabled=False)
    data = prices(1)
    setattr(data[0], field, value)
    pipe._set_cache("000001", "price", data)
    assert pipe._get_cached("000001", "price") is None


def test_collection_source_roundtrip(tmp_path, monkeypatch):
    from data.base import DataSource

    class ShortSource(DataSource):
        def supports(self, market, data_type):
            return data_type == "price"

        def fetch(self, symbol, **kwargs):
            result = prices(19)
            for item in result:
                item._data_source = "测试实际源"
            return result

    reg = Registry()
    reg.register_data_source(ShortSource())
    pipe = Pipeline(reg, Config(config_dir=tmp_path), llm_enabled=False)
    first = pipe.collect("000001", "测试", data_types=["price"])
    second = pipe.collect("000001", "测试", data_types=["price"])
    assert first.collection_records["price"].cache_status == "live"
    assert second.collection_records["price"].cache_status == "cache"
    assert second.collection_records["price"].source == "测试实际源"
    assert (
        second.collection_records["price"].collected_at
        == first.collection_records["price"].collected_at
    )
    assert first.collection_records["price"].collected_at.tzinfo is not None


def test_expiry_removes_metadata(tmp_path, monkeypatch):
    cache = CacheManager(tmp_path / "cache.db")
    cache.put("price", "000001", "latest", "[]", ttl_seconds=0, metadata="record")
    monkeypatch.setattr("data.cache.time.time", lambda: 99999999999)
    assert cache.get("price", "000001", "latest") is None
    assert cache.get_metadata("price", "000001", "latest") is None


def test_stale_retains_original_source(tmp_path):
    from data.base import DataSource
    from data.schemas import DataCollectionRecord, NewsData

    class EmptySource(DataSource):
        def supports(self, market, data_type):
            return data_type == "news"

        def fetch(self, symbol, **kwargs):
            return [NewsData(symbol=symbol, date=date(2026, 1, 2), headlines=[])]

    reg = Registry()
    reg.register_data_source(EmptySource())
    pipe = Pipeline(reg, Config(config_dir=tmp_path), llm_enabled=False)
    original = DataCollectionRecord(
        source="原数据源", collected_at=datetime.now().astimezone(), cache_status="live"
    )
    data = [
        NewsData(
            symbol="000001",
            date=date(2026, 1, 1),
            headlines=["旧新闻1", "旧新闻2", "旧新闻3"],
        )
    ]
    pipe._set_cache("000001", "news", data, original)
    with pipe._cache._get_conn() as conn:
        conn.execute("UPDATE cache SET created_at=0")
    ctx = pipe.collect("000001", "测试", data_types=["news"])
    record = ctx.collection_records["news"]
    assert record.cache_status == "stale"
    assert record.source == "原数据源"
    assert record.collected_at == original.collected_at
    assert record.error_kind == "live_unavailable"


def test_failed_source_summary_safe(tmp_path, monkeypatch):
    from data.base import DataSource

    class FailedSource(DataSource):
        def supports(self, market, data_type):
            return data_type == "price"

        def fetch(self, symbol, **kwargs):
            raise TimeoutError("SDK secret-token endpoint")

    reg = Registry()
    reg.register_data_source(FailedSource())
    pipe = Pipeline(reg, Config(config_dir=tmp_path), llm_enabled=False)
    monkeypatch.setattr("core.pipeline.time.sleep", lambda _: None)
    ctx = pipe.collect("000001", "测试", data_types=["price"])
    record = ctx.collection_records["price"]
    assert record.error_kind == "timeout"
    assert record.error_message == "数据请求超时"
    assert record.collected_at is None
    assert "secret" not in record.model_dump_json()


def test_empty_major_data_skips_sentiment_llm(tmp_path, monkeypatch):
    from data.schemas import RawSentimentData, RawSentimentItem
    from llm.base import LLMBackend

    class CountingBackend(LLMBackend):
        def __init__(self):
            self.calls = 0

        @property
        def model_name(self):
            return "test"

        def generate(self, prompt, **kwargs):
            self.calls += 1
            return "[]"

    reg = Registry()
    backend = CountingBackend()
    reg.register_llm_backend(backend, provider="openai")
    cfg = Config(config_dir=tmp_path)
    cfg.data["llm"]["provider"] = "openai"
    pipe = Pipeline(reg, cfg, llm_enabled=True)
    ctx = AnalysisContext(
        symbol="000001",
        name="测试",
        raw_sentiment=RawSentimentData(
            symbol="000001",
            fetch_date=date(2026, 1, 1),
            items=[
                RawSentimentItem(
                    title=f"新闻{i}", source="news", publish_date=date(2026, 1, 1)
                )
                for i in range(3)
            ],
        ),
    )
    monkeypatch.setattr(pipe, "collect", lambda *args, **kwargs: ctx)
    _, commentary, _ = pipe.run("000001", "测试")
    assert commentary == {}
    assert ctx.enriched_sentiment.total_count == 3
    assert backend.calls == 0


@pytest.mark.parametrize("count,level", [(29, "insufficient"), (30, "partial")])
def test_loss_pb_history_threshold(count, level, monkeypatch):
    from data.enrichers.valuation_enricher import ValuationEnricher
    from data.schemas import FinancialData, ValuationData

    ctx = PriceEnricher().enrich(
        AnalysisContext(
            symbol="000001",
            name="测试",
            price_data=prices(count),
            financial_data=[
                FinancialData(
                    symbol="000001",
                    fiscal_quarter=date(2025, month, 28),
                    revenue=100,
                    net_profit=-10,
                    total_equity=1000,
                )
                for month in (3, 6, 9, 12)
            ],
            valuation_data=ValuationData(symbol="000001", date=date(2026, 1, 1), pb=1),
        )
    )
    monkeypatch.setattr(ValuationEnricher, "_get_total_shares", lambda *args: 100)
    ValuationEnricher().enrich(ctx)
    assert ctx.sufficiency.valuation.level.value == level
    assert ctx.enriched_valuation.pb_percentile is None
    assert ctx.enriched_valuation.pe_percentile is None


def test_real_adapter_timeout_record(tmp_path, monkeypatch):
    from requests.exceptions import ReadTimeout

    from data.akshare import AkShareAdapter

    def timeout(**kwargs):
        raise ReadTimeout("SDK secret endpoint")

    monkeypatch.setattr("data.akshare._ak_daily", timeout)
    monkeypatch.setattr("data.akshare._ak_hist", timeout)
    monkeypatch.setattr("core.pipeline.time.sleep", lambda _: None)
    reg = Registry()
    reg.register_data_source(AkShareAdapter())
    pipe = Pipeline(reg, Config(config_dir=tmp_path), llm_enabled=False)
    ctx = pipe.collect("000001", "测试", data_types=["price"])
    record = ctx.collection_records["price"]
    assert record.error_kind == "timeout"
    assert record.source == "东方财富行情（前复权）"
    assert record.error_message == "数据请求超时"
    assert "secret" not in record.model_dump_json()

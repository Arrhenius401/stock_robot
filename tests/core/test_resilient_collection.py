"""财务、行情、A股新闻的降级重试和独立参考回归。"""
from datetime import UTC, datetime, timedelta

import pytest

from core.pipeline import Pipeline
from core.registry import Registry
from data.base import DataSource
from data.schemas import (
    DataCollectionRecord,
    FinancialData,
    NewsData,
    PriceData,
    RawSentimentData,
    RawSentimentItem,
)
from utils.config import Config


class Source(DataSource):
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.diagnostic: DataCollectionRecord | None = None

    def supports(self, market, data_type):
        return True

    def fetch(self, symbol, **kwargs):
        self.calls.append(kwargs)
        return self.rows

    def get_collection_error(self):
        return self.diagnostic


def rows(kind, symbol, *, partial=False):
    today = datetime.now().astimezone().date()
    if kind == "financial":
        if partial:
            return [FinancialData(symbol=symbol, fiscal_quarter=today, revenue=101,
                                  financial_selection_status="partial")]
        return [FinancialData(symbol=symbol, fiscal_quarter=today-timedelta(days=i*90),
            revenue=100, net_profit=10, total_assets=100, total_equity=50, operating_cash_flow=12,
            basic_eps=1, report_currency="CNY", financial_selection_status="complete") for i in range(4)]
    if kind == "price":
        return [PriceData(symbol=symbol, trade_date=today-timedelta(days=i), open=10, high=11,
                          low=9, close=10, volume=100) for i in reversed(range(20 if partial else 150))]
    titles = [f"{'新' if partial else '旧'}消息{i}" for i in range(3)]
    news = NewsData(symbol=symbol, date=today, headlines=titles)
    news._raw_sentiment = RawSentimentData(symbol=symbol, fetch_date=today, items=[
        RawSentimentItem(title=title, source="news", publish_date=today) for title in titles])
    return [news]


@pytest.fixture(autouse=True)
def no_network(mocker):
    mocker.patch("core.pipeline.time.sleep")
    mocker.patch("requests.sessions.Session.request", side_effect=AssertionError("回归禁止联网"))


def setup(tmp_path, kind, market="a-shares"):
    symbol = "00700.HK" if market == "hk" else "000001"
    source = Source(rows(kind, symbol))
    registry = Registry()
    registry.register_data_source(source)
    pipeline = Pipeline(registry, Config(config_dir=tmp_path), llm_enabled=False)
    return pipeline, source, symbol


def collect(pipeline, symbol, kind, market="a-shares", refresh=False):
    return pipeline.collect(symbol, "测试", market=market, data_types=[kind], refresh_cache=refresh)


@pytest.mark.parametrize(("kind", "market"), [
    ("financial", "a-shares"), ("financial", "hk"), ("price", "a-shares"), ("price", "hk"), ("news", "a-shares")])
def test_partial_retries_after_30_minutes_and_keeps_independent_complete_reference(tmp_path, mocker, kind, market):
    clock = mocker.patch("data.cache.time.time", return_value=1000)
    pipeline, source, symbol = setup(tmp_path, kind, market)
    old = collect(pipeline, symbol, kind, market)
    source.rows = rows(kind, symbol, partial=True)
    source.diagnostic = DataCollectionRecord(error_kind="timeout", error_message="补充接口失败")
    first = collect(pipeline, symbol, kind, market, refresh=True)
    second = collect(pipeline, symbol, kind, market)
    assert len(source.calls) == 2
    assert second.collection_records[kind].cache_state == "degraded"
    assert kind in first.reference_data and kind in second.reference_data
    reference = second.reference_data[kind]
    assert reference.record.collected_at == old.collection_records[kind].collected_at
    assert reference.record.source
    if kind == "financial":
        assert second.financial_data and second.financial_data[0].total_assets is None
        assert reference.financial_data[0].total_assets == 100
    elif kind == "price":
        assert len(second.price_data or []) == 20
        assert len(reference.price_data) == 150
    else:
        assert second.news_data and second.news_data.headlines[0].startswith("新")
        assert reference.news_data and reference.news_data.headlines[0].startswith("旧")
    entry = pipeline._cache.get_entry(kind, symbol, "latest")
    assert entry and entry.ttl_seconds == 1800
    clock.return_value = 2801
    collect(pipeline, symbol, kind, market)
    assert len(source.calls) == 3


@pytest.mark.parametrize("kind", ["financial", "price", "news"])
def test_expired_refresh_failure_never_feeds_old_reference_to_current_context(tmp_path, mocker, kind):
    clock = mocker.patch("data.cache.time.time", return_value=1000)
    pipeline, source, symbol = setup(tmp_path, kind)
    old = collect(pipeline, symbol, kind)
    clock.return_value = 1000+604801
    source.rows = []
    for _ in range(2):
        ctx = collect(pipeline, symbol, kind)
        assert getattr(ctx, {"financial":"financial_data", "price":"price_data", "news":"news_data"}[kind]) is None
        assert ctx.reference_data[kind].record.collected_at == old.collection_records[kind].collected_at
    # 首次失败按既有上限尝试两轮，失败等待内不重复网络请求。
    assert len(source.calls) == 3
    assert pipeline._cache.get_entry(kind, symbol, "last_complete") is not None


@pytest.mark.parametrize(("kind", "max_age"), [("financial",30*86400), ("price",7*86400), ("news",86400)])
@pytest.mark.parametrize("offset", [-1, 1])
def test_reference_age_uses_original_acquisition_time(tmp_path, kind, max_age, offset):
    pipeline, source, symbol = setup(tmp_path, kind)
    collect(pipeline, symbol, kind)
    record = DataCollectionRecord(source="已验证来源", cache_state="complete",
        collected_at=datetime.now().astimezone()-timedelta(seconds=max_age+offset))
    with pipeline._cache._get_conn() as conn:
        conn.execute("UPDATE cache_metadata SET metadata=? WHERE data_type=? AND symbol=?",
                     (record.model_dump_json(), kind, symbol))
    source.rows = []
    ctx = collect(pipeline, symbol, kind, refresh=True)
    assert (kind in ctx.reference_data) is (offset < 0)


def test_unknown_reference_time_is_not_invented(tmp_path):
    pipeline, source, symbol = setup(tmp_path, "financial")
    collect(pipeline, symbol, "financial")
    with pipeline._cache._get_conn() as conn:
        conn.execute("UPDATE cache_metadata SET metadata=?", (DataCollectionRecord(source="旧来源").model_dump_json(),))
    source.rows = []
    ctx = collect(pipeline, symbol, "financial", refresh=True)
    assert not ctx.reference_data


def test_failed_without_previous_data_is_throttled_but_force_refresh_bypasses_wait(tmp_path, mocker):
    clock = mocker.patch("data.cache.time.time", return_value=1000)
    pipeline, source, symbol = setup(tmp_path, "financial")
    source.rows = []
    for _ in range(2):
        assert collect(pipeline, symbol, "financial").financial_data is None
    assert len(source.calls) == 2
    collect(pipeline, symbol, "financial", refresh=True)
    assert len(source.calls) == 4
    clock.return_value = 2801
    collect(pipeline, symbol, "financial")
    assert len(source.calls) == 6


def test_successful_empty_news_has_normal_ttl_and_is_not_a_network_failure(tmp_path):
    pipeline, source, symbol = setup(tmp_path, "news")
    empty = NewsData(symbol=symbol, date=datetime.now().astimezone().date())
    empty._raw_sentiment = RawSentimentData(symbol=symbol, fetch_date=empty.date, items=[])
    source.rows = [empty]
    for _ in range(2):
        ctx = collect(pipeline, symbol, "news")
        assert ctx.news_data and not ctx.news_data.headlines
        assert ctx.collection_records["news"].cache_state == "complete"
    assert len(source.calls) == 1
    entry = pipeline._cache.get_entry("news", symbol, "latest")
    assert entry and entry.ttl_seconds == 21600


def test_explicit_normal_short_history_does_not_trigger_frequent_retry(tmp_path):
    pipeline, source, symbol = setup(tmp_path, "price")
    source.rows = rows("price", symbol, partial=True)
    source.diagnostic = DataCollectionRecord(cache_state="not_applicable")
    for _ in range(2):
        collect(pipeline, symbol, "price")
    assert len(source.calls) == 1
    entry = pipeline._cache.get_entry("price", symbol, "latest")
    assert entry and entry.ttl_seconds == 86400


def test_degraded_ttl_cannot_be_infinite(tmp_path):
    pipeline, source, symbol = setup(tmp_path, "financial")
    pipeline._config.set("data.cache_ttl.quarterly", -1)
    source.rows = rows("financial", symbol, partial=True)
    collect(pipeline, symbol, "financial")
    entry = pipeline._cache.get_entry("financial", symbol, "latest")
    assert entry and entry.ttl_seconds == 1800


@pytest.mark.parametrize(("kind", "ttl"), [("financial", 604800), ("news", 21600)])
def test_explicit_normal_empty_result_uses_normal_ttl(tmp_path, kind, ttl):
    pipeline, source, symbol = setup(tmp_path, kind)
    source.rows = []
    source.diagnostic = DataCollectionRecord(cache_state="not_applicable", error_message="来源已确认暂无披露")
    for _ in range(2):
        ctx = collect(pipeline, symbol, kind)
        assert ctx.collection_records[kind].cache_state == "not_applicable"
        assert not ctx.reference_data
    assert len(source.calls) == 1
    entry = pipeline._cache.get_entry(kind, symbol, "latest")
    assert entry and entry.ttl_seconds == ttl


def test_hk_business_date_is_used_on_a_utc_host(tmp_path, mocker):
    from core.resilient_collection import ResilientCollector, Snapshot
    from data.cache import CacheEntry
    from data.hk_news import HK_TZ

    instant = datetime(2026, 10, 26, 16, 30, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz or UTC)

    mocker.patch("core.resilient_collection.datetime", Clock)
    today = instant.astimezone(HK_TZ).date()
    news = NewsData(symbol="000001", date=today, headlines=["当日消息"])
    news._raw_sentiment = RawSentimentData(symbol=news.symbol, fetch_date=today, items=[
        RawSentimentItem(title="当日消息", source="news", publish_date=today)])
    assert ResilientCollector._valid("news", news.symbol, [news])
    pipeline, _, _ = setup(tmp_path, "news")
    snapshot = Snapshot([news], DataCollectionRecord(source="已验证来源", collected_at=instant-timedelta(minutes=15)),
                        CacheEntry("[]", 1000, 21600, None))
    reference = pipeline._resilient_collector._reference("news", snapshot, "刷新失败")
    assert reference and reference.news_data and reference.news_data.headlines == ["当日消息"]


def test_later_old_response_cannot_replace_newer_same_symbol_collection(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    pipeline, source, symbol = setup(tmp_path, "financial")
    slow_started, release, fast_started = Event(), Event(), Event()
    today = datetime.now().astimezone().date()
    old = rows("financial", symbol)
    for row in old:
        assert isinstance(row, FinancialData)
        row.fiscal_quarter -= timedelta(days=90)
    new = rows("financial", symbol)
    attempts = []

    def fetch(symbol, **kwargs):
        attempts.append(symbol)
        if len(attempts) == 1:
            slow_started.set()
            assert release.wait(5)
            return old
        return new

    source.fetch = fetch

    def second():
        fast_started.set()
        return collect(pipeline, symbol, "financial", refresh=True)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(collect, pipeline, symbol, "financial", "a-shares", True)
        assert slow_started.wait(5)
        later = executor.submit(second)
        assert fast_started.wait(5)
        release.set()
        first.result()
        later.result()
    cached = pipeline._resilient_collector._read("financial", symbol, "latest", "a-shares")
    assert cached and max(row.fiscal_quarter for row in cached.rows) == today


def test_failed_attempt_preserves_newer_partial_facts_as_reference(tmp_path):
    pipeline, source, symbol = setup(tmp_path, "financial")
    collect(pipeline, symbol, "financial")
    partial = rows("financial", symbol, partial=True)
    source.rows = partial
    collect(pipeline, symbol, "financial", refresh=True)
    source.rows = []
    failed = collect(pipeline, symbol, "financial", refresh=True)
    again = collect(pipeline, symbol, "financial")
    for ctx in (failed, again):
        assert ctx.financial_data is None
        assert ctx.reference_data["financial"].financial_data[0].revenue == 101
        assert ctx.reference_data["financial"].financial_data[0].total_assets is None
    complete = pipeline._resilient_collector._read("financial", symbol, "last_complete", "a-shares")
    assert complete and complete.rows[0].revenue == 100


def test_older_complete_response_does_not_hide_newer_partial_report(tmp_path):
    pipeline, source, symbol = setup(tmp_path, "financial")
    source.rows = rows("financial", symbol, partial=True)
    observed = collect(pipeline, symbol, "financial")
    assert observed.financial_data
    older = rows("financial", symbol)
    for row in older:
        assert isinstance(row, FinancialData)
        row.fiscal_quarter -= timedelta(days=90)
    source.rows = older
    ctx = collect(pipeline, symbol, "financial", refresh=True)
    assert ctx.financial_data is None
    assert ctx.reference_data["financial"].financial_data[0].revenue == 101


def test_bulk_commentary_never_receives_reference_values(tmp_path):
    from data.schemas import AnalysisContext, AnalysisResult, DataReference
    from llm.base import LLMBackend

    class RecordingLLM(LLMBackend):
        def __init__(self):
            self.prompts = []

        @property
        def model_name(self):
            return "离线测试"

        def generate(self, prompt, **kwargs):
            self.prompts.append(prompt)
            return "当前事实解读"

    pipeline, _, symbol = setup(tmp_path, "financial")
    model = RecordingLLM()
    pipeline._registry.register_llm_backend(model, provider=pipeline._config.get("llm.provider"))
    reference = DataReference(data_type="financial", record=DataCollectionRecord(
        source="旧参考独有来源", collected_at=datetime.now().astimezone()), financial_data=[FinancialData(
            symbol=symbol, fiscal_quarter=datetime.now().astimezone().date(), revenue=987654321)])
    ctx = AnalysisContext(symbol=symbol, name="测试", reference_data={"financial": reference})
    pipeline._generate_commentary(symbol, "测试", [AnalysisResult(
        dimension="technical", status="ok", summary="当前技术事实", score=6)], context=ctx)
    assert len(model.prompts) == 1
    assert "987654321" not in model.prompts[0] and "旧参考独有来源" not in model.prompts[0]


@pytest.mark.parametrize("legacy", ["missing", "unknown_time", "naive", "older"])
def test_legacy_provenance_cannot_erase_good_complete_reference(tmp_path, legacy):
    pipeline, source, symbol = setup(tmp_path, "financial")
    baseline = collect(pipeline, symbol, "financial")
    entry = pipeline._cache.get_entry("financial", symbol, "latest")
    assert entry
    stamp = (datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None) if legacy == "naive" else
             datetime.now().astimezone()-timedelta(days=1) if legacy == "older" else None)
    metadata = None if legacy == "missing" else DataCollectionRecord(
        source="旧元数据", collected_at=stamp).model_dump_json()
    pipeline._cache.put("financial", symbol, "latest", entry.value, metadata=metadata)
    source.rows = rows("financial", symbol, partial=True)
    for refresh in (True, False):
        ctx = collect(pipeline, symbol, "financial", refresh=refresh)
        assert ctx.reference_data["financial"].record == baseline.collection_records["financial"]
    preserved = pipeline._resilient_collector._read("financial", symbol, "last_complete", "a-shares")
    assert preserved and preserved.record.source == "Source"

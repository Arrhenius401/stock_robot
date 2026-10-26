"""集成：采集→健康缓存→断路器 全流程"""
from datetime import date, timedelta

import pytest

from core.pipeline import Pipeline
from core.registry import Registry
from data.base import DataSource
from data.schemas import FinancialData, IndustryData, PriceData
from utils.config import Config


class FakeSource(DataSource):
    """模拟数据源：可配置各类型返回值与失败次数（前 N 次调用抛网络错误）"""

    def __init__(self, returns: dict[str, list], fail_first: int = 0):
        self._returns = returns
        self._fail_first = fail_first
        self._calls: dict[str, int] = {}

    def supports(self, market: str, data_type: str) -> bool:
        return data_type in self._returns

    def fetch(self, symbol: str, **kwargs) -> list:
        data_type = kwargs.get("data_type", "price")
        self._calls[data_type] = self._calls.get(data_type, 0) + 1
        if self._calls[data_type] <= self._fail_first:
            raise ConnectionError("mock 网络错误")
        return self._returns[data_type]


def test_pipeline_collect_healthy_cache_and_breaker(tmp_path, mocker):
    """采集→健康缓存→断路器 全流程：健康数据写缓存并命中，退化源触发断路器"""
    # 跳过错峰/重试 sleep，加速测试
    mocker.patch("core.pipeline.time.sleep")

    ind = IndustryData(
        symbol="000001", industry="银行", sector="金融", peers=[],
        peer_scope="申万二级", peer_industry="银行", top_peers=[],
    )
    prices = [PriceData(symbol="000001", trade_date=date(2025, 7, 1) + timedelta(days=i),
                        open=10, high=10.1, low=9.9, close=10, volume=1000)
              for i in range(250)]
    fin = FinancialData(symbol="000001", fiscal_quarter=date(2026, 6, 30),
                        net_profit=10e8, basic_eps=0.5)

    # fail_first=1：首次尝试失败、重试成功——同时覆盖重试路径与健康数据写入缓存
    source = FakeSource({
        "price": prices, "financial": [fin], "industry": [ind],
        "valuation": [], "news": [],
    }, fail_first=1)

    reg = Registry()
    reg.register_data_source(source)
    # 缓存路径由 Config(config_dir=...) 派生，tmp_path 隔离真实缓存
    pipe = Pipeline(registry=reg, config=Config(config_dir=tmp_path), llm_enabled=False)

    # 第一次收集：财务健康数据写入通用缓存
    ctx1 = pipe.collect("000001", "平安银行")
    assert ctx1.financial_data == [fin]

    # 缓存命中：第二次不再请求源头
    source._calls = {}
    ctx2 = pipe.collect("000001", "平安银行")
    assert ctx2.financial_data == [fin]
    assert source._calls.get("financial", 0) == 0

    # 断路器：退化源（financial 返回 []）失败 3 次后打开，第 4 次不再请求源头
    # 注意：fetch 每次 collect 有 2 次尝试，3 次 collect 共 6 次调用
    bad = FakeSource({"financial": []}, fail_first=0)
    reg2 = Registry()
    reg2.register_data_source(bad)
    # 独立 config_dir 隔离缓存，避免命中上一段写入的财务缓存
    pipe2 = Pipeline(registry=reg2, config=Config(config_dir=tmp_path / "cfg2"),
                     llm_enabled=False)
    clock = mocker.patch("data.cache.time.time", return_value=1000)
    for index in range(3):
        clock.return_value = 1000 + index * 1801
        pipe2.collect("000001", "平安银行")
    assert bad._calls["financial"] == 6  # 3 次 collect × 每次 2 次尝试
    pipe2.collect("000001", "平安银行")
    assert bad._calls["financial"] == 6  # 断路器打开，不再请求源头


def test_degenerate_result_counts_as_failure(tmp_path, mocker):
    """退化结果（industry=未知）不落缓存且计为失败，连续 3 次触发断路器"""
    mocker.patch("core.pipeline.time.sleep")

    bad_industry = IndustryData(symbol="000001", industry="未知", sector="", peers=[], top_peers=[])
    source = FakeSource({"industry": [bad_industry]}, fail_first=0)
    reg = Registry()
    reg.register_data_source(source)
    pipe = Pipeline(registry=reg, config=Config(config_dir=tmp_path / "cfg3"),
                    llm_enabled=False)

    # 退化结果仍返回给分析（维度判数据不足），但断路器计数
    for _ in range(3):
        ctx = pipe.collect("000001", "平安银行")
        assert ctx.industry_data is not None
    assert pipe._breaker.is_open("000001", "industry") is True
    # 退化数据未落缓存（_get_cached 返回 None）
    assert pipe._get_cached("000001", "industry") is None


def test_pipeline_updates_missing_classification_from_sw_source(tmp_path, mocker):
    """显式缺失分类只通过申万来源更新，东财观测值不参与写回。"""
    from data.industry_classifier import IndustryClassification

    mocker.patch("core.pipeline.time.sleep")
    # 分类器返回显式缺失状态。
    fake_cls = mocker.patch("data.industry_classifier.IndustryClassifier").return_value
    fake_cls.lookup.return_value = IndustryClassification(
        symbol="000001", sw_level1="", sw_level2="", style_category="",
        mapping_status="missing")
    update = mocker.patch("data.industry_mapping_builder.update_symbol", return_value={
        "symbol": "000001", "sw_level1": "银行", "sw_level2": "银行",
        "style_category": "大金融", "action": "updated",
    })

    ind = IndustryData(
        symbol="000001", industry="银行", sector="金融", peers=[],
        peer_scope="申万二级", peer_industry="银行", top_peers=[],
    )
    source = FakeSource({"industry": [ind], "price": [], "financial": [],
                         "valuation": [], "news": []})
    reg = Registry()
    reg.register_data_source(source)
    pipe = Pipeline(registry=reg, config=Config(config_dir=tmp_path), llm_enabled=False)

    ctx = pipe.collect("000001", "平安银行")
    assert ctx.sw_industry == "银行"
    assert ctx.style_category == "大金融"
    update.assert_called_once_with("000001", retries=1, timeout=5)


def test_pipeline_passes_sw_levels_to_industry_source(tmp_path, mocker):
    """行业采集前已完成申万分类，数据源只能按该口径构建同行池。"""
    from data.industry_classifier import IndustryClassification

    mocker.patch("core.pipeline.time.sleep")
    fake_cls = mocker.patch("data.industry_classifier.IndustryClassifier").return_value
    fake_cls.lookup.return_value = IndustryClassification(
        symbol="002714", sw_level1="农林牧渔", sw_level2="养殖业",
        style_category="必选消费", mapping_status="verified",
    )

    captured: dict[str, object] = {}

    class IndustrySource(FakeSource):
        def fetch(self, symbol: str, **kwargs) -> list:
            captured.update(kwargs)
            return [IndustryData(
                symbol=symbol, industry="养殖业", sector="农林牧渔",
                peers=["002157"], peer_scope="申万二级", peer_industry="养殖业",
            )]

    reg = Registry()
    reg.register_data_source(IndustrySource({"industry": []}))
    pipe = Pipeline(registry=reg, config=Config(config_dir=tmp_path), llm_enabled=False)

    ctx = pipe.collect("002714", "牧原股份", data_types=["industry"])

    assert captured == {
        "data_type": "industry", "market": "a-shares",
        "sw_level1": "农林牧渔", "sw_level2": "养殖业",
        "comparison_cache": pipe._cache, "refresh_cache": False,
        "comparison_cache_only": False,
    }
    assert ctx.sw_industry == "农林牧渔"
    assert ctx.sw_industry_level2 == "养殖业"


def test_hk_valuation_health_requires_positive_metric():
    from data.schemas import ValuationData
    pipe = Pipeline.__new__(Pipeline)
    negative_only = ValuationData(symbol="09888.HK", date=date(2026, 10, 23),
                                  pe_ttm=-56.62)
    assert pipe._is_healthy("valuation", [negative_only]) is False
    with_pb = ValuationData(symbol="09888.HK", date=date(2026, 10, 23),
                            pe_ttm=-56.62, pb=0.66)
    assert pipe._is_healthy("valuation", [with_pb]) is True
    a_share = ValuationData(symbol="000001", date=date(2026, 10, 23), pe_ttm=-1)
    assert pipe._is_healthy("valuation", [a_share]) is True


def test_hk_degraded_valuation_uses_short_ttl(tmp_path, mocker):
    from data.schemas import ValuationData
    pipe = Pipeline(registry=Registry(), config=Config(config_dir=tmp_path), llm_enabled=False)
    put = mocker.spy(pipe._cache, "put")
    row = ValuationData(symbol="09888.HK", date=date(2026, 10, 23), pb=0.66,
                        cache_degraded=True)
    pipe._set_cache("09888.HK", "valuation", [row])
    assert put.call_args.kwargs["ttl_seconds"] == 1800


def test_hk_degraded_does_not_overwrite_complete_history(tmp_path, mocker):
    from data.schemas import ValuationData
    pipe = Pipeline(registry=Registry(), config=Config(config_dir=tmp_path), llm_enabled=False)
    old = ValuationData(symbol="09888.HK", date=date(2026, 10, 22), pb=0.65,
                        pb_history={date(2026, 10, 22): 0.65},
                        history_source="百度港股估值（近一年）")
    pipe._set_cache("09888.HK", "valuation", [old])
    new = ValuationData(symbol="09888.HK", date=date(2026, 10, 23), pb=0.66,
                        cache_degraded=True)
    pipe._set_cache("09888.HK", "valuation", [new])
    cached = pipe._get_stale_cached("09888.HK", "valuation", date_key="last_complete")
    assert cached is not None and cached[0].pb_history


def test_merge_hk_valuation_fills_missing_history_and_ps():
    from data.schemas import ValuationData
    old = ValuationData(
        symbol="09888.HK", date=date(2026, 10, 22), pb=0.65,
        ps_ttm=1.4, ps_as_of=date(2026, 10, 22), ps_source="东方财富港股估值对比",
        pb_history={date(2026, 10, 22): 0.65},
        history_source="百度港股估值（近一年）")
    new = ValuationData(symbol="09888.HK", date=date(2026, 10, 23),
                        pe_ttm=-56.62, pb=0.66, cache_degraded=True)
    merged, used_stale = Pipeline._merge_hk_valuation([old], [new])
    assert used_stale is True
    assert merged is not None
    assert merged[0].pb_history == {date(2026, 10, 22): 0.65}
    assert merged[0].ps_ttm == 1.4
    assert any("沿用上次成功数据" in note for note in merged[0].valuation_notes)


def test_stale_valuation_used_when_live_returns_none(tmp_path, mocker):
    from data.schemas import ValuationData
    mocker.patch("core.pipeline.time.sleep")
    reg = Registry()
    reg.register_data_source(FakeSource({"valuation": []}))
    pipe = Pipeline(registry=reg, config=Config(config_dir=tmp_path), llm_enabled=False)
    old = ValuationData(symbol="09888.HK", date=date(2026, 10, 22), pb=0.65,
                        pb_history={date(2026, 10, 22): 0.65},
                        history_source="百度港股估值（近一年）")
    pipe._set_cache("09888.HK", "valuation", [old])
    ctx = pipe.collect("09888.HK", "百度集团", market="hk", data_types=["valuation"],
                       refresh_cache=True)
    assert ctx.valuation_data is not None and ctx.valuation_data.pb == 0.65
    assert ctx.collection_records["valuation"].cache_status == "stale"


def test_expired_hk_valuation_survives_repeated_refresh_failures(tmp_path, mocker):
    """读取新鲜缓存不能删掉下一次仍需使用的过期兜底及采集时间。"""
    from datetime import datetime

    from data.schemas import DataCollectionRecord, ValuationData

    mocker.patch("core.pipeline.time.sleep")
    clock = mocker.patch("data.cache.time.time", return_value=1000)
    reg = Registry()
    reg.register_data_source(FakeSource({"valuation": []}))
    pipe = Pipeline(registry=reg, config=Config(config_dir=tmp_path), llm_enabled=False)
    row = ValuationData(symbol="09888.HK", date=date(2026, 10, 23), pb=0.66)
    record = DataCollectionRecord(source="东财", collected_at=datetime.now().astimezone())
    pipe._set_cache(row.symbol, "valuation", [row], record)
    clock.return_value = 1000 + 86401
    for _ in range(2):
        ctx = pipe.collect(row.symbol, "百度", market="hk", data_types=["valuation"])
        assert ctx.valuation_data is not None
        assert ctx.collection_records["valuation"].cache_status == "stale"
        assert ctx.collection_records["valuation"].collected_at == record.collected_at
    assert pipe._cache.get_stale("valuation", row.symbol, "latest") is not None


def test_degraded_refresh_preserves_complete_ps_cache_even_after_merge(tmp_path):
    from data.schemas import ValuationData

    pipe = Pipeline(registry=Registry(), config=Config(config_dir=tmp_path), llm_enabled=False)
    old = ValuationData(symbol="09888.HK", date=date(2026, 10, 23), pb=0.66,
                        ps_ttm=1.44, ps_as_of=date(2026, 10, 23),
                        ps_source="东方财富港股估值对比")
    pipe._set_cache(old.symbol, "valuation", [old])
    new = old.model_copy(update={"pb": 0.67, "cache_degraded": True})
    pipe._set_cache(old.symbol, "valuation", [new])
    cached = pipe._get_stale_cached(old.symbol, "valuation", date_key="last_complete")
    assert cached and cached[0].pb == 0.66 and not cached[0].cache_degraded
    latest = pipe._get_cached(old.symbol, "valuation")
    assert latest and latest[0].pb == 0.67 and latest[0].cache_degraded


def test_merge_hk_valuation_rejects_unknown_history_and_ps_source():
    from data.schemas import ValuationData

    old = ValuationData(symbol="09888.HK", date=date(2026, 10, 23), pb=0.66,
                        pb_history={date(2026, 10, 23): 0.66}, history_source="其他来源",
                        ps_ttm=1.44, ps_as_of=date(2026, 10, 23), ps_source="未知")
    new = ValuationData(symbol="09888.HK", date=date(2026, 10, 26), pb=0.67,
                        cache_degraded=True)
    merged, used = Pipeline._merge_hk_valuation([old], [new])
    assert not used and merged and not merged[0].pb_history and merged[0].ps_ttm is None


def test_structural_ps_missing_does_not_revive_old_ps_after_primary_failure(mocker):
    from data.akshare import AkShareAdapter
    from data.schemas import ValuationData

    mocker.patch("data.akshare.stock_snapshot", side_effect=TimeoutError)
    mocker.patch("data.akshare.valuation_comparison_snapshot", return_value={
        "REPORT_DATE": "2026-10-23", "PE_TTM": -56, "PB_MQR": 0.66, "PS_TTM": None})
    mocker.patch("data.akshare.baidu_metric", return_value={date(2026, 10, 23): 1.0})
    current = AkShareAdapter().fetch("09888.HK", market="hk", data_type="valuation")
    old = ValuationData(symbol="09888.HK", date=date(2026, 10, 22), pb=0.65,
                        ps_ttm=1.44, ps_as_of=date(2026, 10, 22),
                        ps_source="东方财富港股估值对比")
    merged, _ = Pipeline._merge_hk_valuation([old], current)
    assert merged and merged[0].ps_ttm is None


def test_forced_refresh_negative_pe_is_retained_on_next_cached_read(tmp_path, mocker):
    from data.schemas import ValuationData

    mocker.patch("core.pipeline.time.sleep")
    day = date(2026, 10, 23)
    old = ValuationData(symbol="09888.HK", date=day, pe_ttm=20, pb=0.65,
                        pe_history={day: 20}, pb_history={day: 0.65},
                        history_source="百度港股估值（近一年）")
    new = ValuationData(symbol=old.symbol, date=day, pe_ttm=-56, pb=0.66,
                        cache_degraded=True, failed_components=["pe_history", "pb_history"])
    source = FakeSource({"valuation": [new]})
    reg = Registry()
    reg.register_data_source(source)
    pipe = Pipeline(registry=reg, config=Config(config_dir=tmp_path), llm_enabled=False)
    pipe._set_cache(old.symbol, "valuation", [old])
    for refresh in (True, False):
        ctx = pipe.collect(old.symbol, "百度", market="hk", data_types=["valuation"],
                           refresh_cache=refresh)
        assert ctx.valuation_data and ctx.valuation_data.pe_ttm == -56
        assert ctx.valuation_data.pb_history == old.pb_history
    assert source._calls["valuation"] == 1
    complete = pipe._get_stale_cached(old.symbol, "valuation", date_key="last_complete")
    assert complete and complete[0].pe_ttm == 20


def test_degraded_ttl_is_bounded_when_daily_cache_is_permanent(tmp_path, mocker):
    from data.schemas import ValuationData

    config = Config(config_dir=tmp_path)
    config.set("data.cache_ttl.daily", -1)
    pipe = Pipeline(registry=Registry(), config=config, llm_enabled=False)
    put = mocker.spy(pipe._cache, "put")
    pipe._set_cache("09888.HK", "valuation", [ValuationData(
        symbol="09888.HK", date=date(2026, 10, 23), pb=0.66, cache_degraded=True)])
    assert put.call_args.kwargs["ttl_seconds"] == 1800


@pytest.mark.parametrize("degraded", [False, True])
def test_legal_nonpositive_valuation_does_not_trip_source_breaker(tmp_path, mocker, degraded):
    from data.schemas import ValuationData

    mocker.patch("core.pipeline.time.sleep")
    row = ValuationData(symbol="09888.HK", date=date(2026, 10, 23), pe_ttm=-56, pb=-1,
                        cache_degraded=degraded, failed_components=["comparison"] if degraded else [])
    source = FakeSource({"valuation": [row]})
    reg = Registry()
    reg.register_data_source(source)
    pipe = Pipeline(registry=reg, config=Config(config_dir=tmp_path), llm_enabled=False)
    pipe._set_cache(row.symbol, "valuation", [row.model_copy(update={
        "pe_ttm": 20, "pb": 1, "cache_degraded": False, "failed_components": []})])
    for _ in range(4):
        ctx = pipe.collect(row.symbol, "百度", market="hk", data_types=["valuation"], refresh_cache=_ == 0)
        assert ctx.valuation_data and ctx.valuation_data.pe_ttm == -56
    assert source._calls["valuation"] == 4
    assert not pipe._breaker.is_open(row.symbol, "valuation")
    assert pipe._get_cached(row.symbol, "valuation") is None


def test_latest_nonpositive_facts_survive_next_network_failure(tmp_path, mocker):
    from data.schemas import ValuationData

    mocker.patch("core.pipeline.time.sleep")
    row = ValuationData(symbol="09888.HK", date=date(2026, 10, 23), pe_ttm=-56, pb=-1)
    source = FakeSource({"valuation": [row]})
    reg = Registry()
    reg.register_data_source(source)
    pipe = Pipeline(registry=reg, config=Config(config_dir=tmp_path), llm_enabled=False)
    pipe._set_cache(row.symbol, "valuation", [row.model_copy(update={"pe_ttm": 20, "pb": 1})])
    pipe.collect(row.symbol, "百度", market="hk", data_types=["valuation"], refresh_cache=True)
    source._returns["valuation"] = []
    ctx = pipe.collect(row.symbol, "百度", market="hk", data_types=["valuation"])
    assert ctx.valuation_data and ctx.valuation_data.pe_ttm == -56
    assert ctx.collection_records["valuation"].cache_status == "stale"


def test_old_positive_curve_fallback_does_not_override_newer_negative_snapshot():
    from data.schemas import ValuationData

    old = ValuationData(symbol="09888.HK", date=date(2026, 10, 23), pe_ttm=-56, pb=0.66,
                        pe_as_of=date(2026, 10, 23), pb_as_of=date(2026, 10, 23))
    new = ValuationData(symbol=old.symbol, date=date(2026, 8, 17), pe_ttm=183, pb=0.8,
                        pe_as_of=date(2026, 8, 17), pb_as_of=date(2026, 8, 17),
                        cache_degraded=True, failed_components=["snapshot", "comparison"])
    new._data_source = "百度港股估值曲线末值（非实时快照）"
    merged, used = Pipeline._merge_hk_valuation([old], [new])
    assert used and merged and merged[0].pe_ttm == -56 and merged[0].pb == 0.66
    assert merged[0].pe_as_of == date(2026, 10, 23)
    assert new.pe_ttm == 183  # 不修改采集器原始返回或旧缓存对象。


def test_report_and_cache_use_same_merge_after_structural_ps_disappearance(tmp_path, mocker):
    from data.schemas import ValuationData

    mocker.patch("core.pipeline.time.sleep")
    day = date(2026, 10, 23)
    old = ValuationData(symbol="09888.HK", date=day, pb=0.66, ps_ttm=1.44,
                        ps_as_of=day, ps_source="东方财富港股估值对比")
    source = FakeSource({"valuation": [old.model_copy(update={
        "ps_ttm": None, "cache_degraded": True, "failed_components": ["comparison"]})]})
    reg = Registry()
    reg.register_data_source(source)
    pipe = Pipeline(registry=reg, config=Config(config_dir=tmp_path), llm_enabled=False)
    pipe._set_cache(old.symbol, "valuation", [old])
    structural = old.model_copy(update={"ps_ttm": None, "ps_as_of": None, "ps_source": "",
                                         "cache_degraded": True, "failed_components": ["snapshot"]})
    pipe._set_cache(old.symbol, "valuation", [structural])
    ctx = pipe.collect(old.symbol, "百度", market="hk", data_types=["valuation"], refresh_cache=True)
    cached = pipe._get_cached(old.symbol, "valuation")
    assert ctx.valuation_data and cached
    assert ctx.valuation_data.ps_ttm == cached[0].ps_ttm is None

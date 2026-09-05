"""集成：采集→健康缓存→断路器 全流程"""
from datetime import date, timedelta

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

    ind = IndustryData(symbol="000001", industry="银行", sector="金融",
                       peers=[], top_peers=[])
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

    # 第一次收集：industry 健康数据写入缓存
    ctx1 = pipe.collect("000001", "平安银行")
    assert ctx1.industry_data is not None
    assert ctx1.industry_data.industry == "银行"

    # 缓存命中：第二次不再请求源头
    source._calls = {}
    ctx2 = pipe.collect("000001", "平安银行")
    assert ctx2.industry_data is not None
    assert ctx2.industry_data.industry == "银行"
    assert source._calls.get("industry", 0) == 0

    # 断路器：退化源（industry 返回 []）失败 3 次后打开，第 4 次不再请求源头
    # 注意：fetch 每次 collect 有 2 次尝试，3 次 collect 共 6 次调用
    bad = FakeSource({"industry": []}, fail_first=0)
    reg2 = Registry()
    reg2.register_data_source(bad)
    # 独立 config_dir 隔离缓存，避免命中上一段写入的 industry 缓存
    pipe2 = Pipeline(registry=reg2, config=Config(config_dir=tmp_path / "cfg2"),
                     llm_enabled=False)
    for _ in range(3):
        pipe2.collect("000001", "平安银行")
    assert bad._calls["industry"] == 6  # 3 次 collect × 每次 2 次尝试
    pipe2.collect("000001", "平安银行")
    assert bad._calls["industry"] == 6  # 断路器打开，不再请求源头


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

    ind = IndustryData(symbol="000001", industry="银行", sector="金融",
                       peers=[], top_peers=[])
    source = FakeSource({"industry": [ind], "price": [], "financial": [],
                         "valuation": [], "news": []})
    reg = Registry()
    reg.register_data_source(source)
    pipe = Pipeline(registry=reg, config=Config(config_dir=tmp_path), llm_enabled=False)

    ctx = pipe.collect("000001", "平安银行")
    assert ctx.sw_industry == "银行"
    assert ctx.style_category == "大金融"
    update.assert_called_once_with("000001", retries=1, timeout=5)

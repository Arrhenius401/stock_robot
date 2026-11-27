import pandas as pd
import pytest


@pytest.fixture
def verified_fx(mocker):
    """与币种折算无关的日期/门禁用例显式采用合成 1:1 汇率，禁止联网。"""
    from data.fx_rates import FxRate

    mocker.patch("data.fx_rates.rate_on", side_effect=lambda currency, target="CNY", *, day:
                 FxRate(currency, target, 1.0, (day,)))
    mocker.patch("data.fx_rates.average_rate", side_effect=lambda currency, start, end, target="CNY":
                 FxRate(currency, target, 1.0, (start, end)))


@pytest.fixture(autouse=True)
def _test_defaults(mocker, tmp_path_factory):
    """全局测试默认：新数据源不可用则自动回退到已被 mock 覆盖的旧数据源"""
    for api in ("abstract", "cash", "debt"):
        mocker.patch(f"akshare.stock_financial_{api}_new_ths",
                     side_effect=ConnectionError("mock: use legacy financial"))
    mocker.patch("akshare.stock_zh_a_daily", side_effect=ConnectionError("mock: use fallback"))
    mocker.patch("akshare.stock_financial_debt_ths", side_effect=ConnectionError("mock: use fallback"))
    # 管道测试不访问新增主营页面；适配器测试直接调用原函数并单独替换HTTP。
    mocker.patch("data.business_profile.load_business_profile", return_value={"status": "unavailable", "main_business": ""})
    mocker.patch("data.latest_quote.load_latest_quote", return_value={"status": "unavailable"})
    mocker.patch("data.latest_quote.load_hk_quote", return_value={"status": "unavailable"})
    mocker.patch("data.us_stock.us_quote", return_value={"price": None})
    mocker.patch("data.us_filings.fetch_filings", return_value=[])
    # 东财个股信息接口（含行业/总股本字段）全局 mock，行业判定与总股本反推不依赖网络
    mocker.patch("akshare.stock_individual_info_em",
                 return_value=pd.DataFrame({"item": ["行业", "总股本"],
                                            "value": ["银行", "194.05亿"]}))

    # 新增来源的成功缓存逐测试隔离，不读写开发者真实运行缓存。
    from data.cache import CacheManager
    cache_path = None

    def source_cache():
        nonlocal cache_path
        if cache_path is None:
            cache_path = tmp_path_factory.mktemp("source-success") / "cache.db"
        return CacheManager(cache_path)

    mocker.patch("data.a_valuation_history._source_cache", side_effect=source_cache)
    mocker.patch("data.us_sec._source_cache", side_effect=source_cache)
    mocker.patch("data.fx_rates._source_cache", side_effect=source_cache)
    mocker.patch("data.fx_rates._request", side_effect=ConnectionError("mock: FX unavailable"))
    mocker.patch("akshare.stock_value_em", side_effect=ConnectionError("mock: valuation unavailable"))
    mocker.patch("akshare.stock_financial_hk_analysis_indicator_em", side_effect=ConnectionError("mock: indicator unavailable"))

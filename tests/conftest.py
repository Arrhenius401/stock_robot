import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _test_defaults(mocker, tmp_path):
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
    # 东财个股信息接口（含行业/总股本字段）全局 mock，行业判定与总股本反推不依赖网络
    mocker.patch("akshare.stock_individual_info_em",
                 return_value=pd.DataFrame({"item": ["行业", "总股本"],
                                            "value": ["银行", "194.05亿"]}))

    # 新增来源的成功缓存逐测试隔离，不读写开发者真实运行缓存。
    from data.cache import CacheManager
    cache = CacheManager(tmp_path / "source-success.db")
    mocker.patch("data.a_valuation_history._source_cache", return_value=cache)
    mocker.patch("data.us_sec._source_cache", return_value=cache)
    mocker.patch("akshare.stock_value_em", side_effect=ConnectionError("mock: valuation unavailable"))
    mocker.patch("akshare.stock_financial_hk_analysis_indicator_em", side_effect=ConnectionError("mock: indicator unavailable"))

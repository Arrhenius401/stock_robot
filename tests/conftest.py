import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _test_defaults(mocker):
    """全局测试默认：新数据源不可用则自动回退到已被 mock 覆盖的旧数据源"""
    mocker.patch("akshare.stock_zh_a_daily", side_effect=ConnectionError("mock: use fallback"))
    mocker.patch("akshare.stock_financial_debt_ths", side_effect=ConnectionError("mock: use fallback"))
    # 东财个股信息接口（含行业/总股本字段）全局 mock，行业判定与总股本反推不依赖网络
    mocker.patch("akshare.stock_individual_info_em",
                 return_value=pd.DataFrame({"item": ["行业", "总股本"],
                                            "value": ["银行", "194.05亿"]}))

import pytest


@pytest.fixture(autouse=True)
def _test_defaults(mocker):
    """全局测试默认：新数据源不可用则自动回退到已被 mock 覆盖的旧数据源"""
    mocker.patch("akshare.stock_zh_a_daily", side_effect=ConnectionError("mock: use fallback"))
    mocker.patch("akshare.stock_individual_basic_info_xq", side_effect=ConnectionError("mock: use fallback"))
    from data.akshare import clear_info_cache
    clear_info_cache()

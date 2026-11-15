"""A/港股标题别名避免泛词误收。"""
import pytest

from data.news_relevance import name_aliases


@pytest.mark.parametrize("name,alias", [("中国平安", "平安"), ("招商银行", "招商"), ("宁德时代", "宁德"), ("贵州茅台", "茅台"), ("腾讯控股", "腾讯"), ("阿里巴巴", "阿里"), ("美团", "美团")])
def test_company_aliases_exclude_generic_words(name, alias):
    aliases = name_aliases(name)
    assert alias in aliases
    assert not set(aliases) & {"中国", "时代", "银行", "控股"}

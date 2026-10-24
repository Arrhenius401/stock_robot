"""港股行业数据携带不可序列化的同行估值，必须绕过通用缓存。"""
from core.pipeline import _cacheable


def test_hk_industry_data_is_not_cached():
    assert _cacheable("hk", "industry") is False
    assert _cacheable("hk", "valuation") is True
    assert _cacheable("a-shares", "industry") is True

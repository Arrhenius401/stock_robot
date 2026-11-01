"""两市场的同行证据使用专用缓存，必须绕过旧行业通用缓存。"""
from core.pipeline import _cacheable


def test_hk_industry_data_is_not_cached():
    assert _cacheable("hk", "industry") is False
    assert _cacheable("hk", "valuation") is True
    assert _cacheable("a-shares", "industry") is False

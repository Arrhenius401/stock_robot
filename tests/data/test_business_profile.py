"""主营资料的证券身份、缓存与网络失败边界。"""
from types import SimpleNamespace

import pytest

from data.business_profile import load_business_profile, parse_profile
from data.cache import CacheManager

PAGE = '<title>潍柴动力(000338)</title><ul class="main_intro_list"><li>主营业务：动力系统、商用车。</li><li>产品名称：发动机</li><li>经营范围：不可作为主营</li></ul>'


def test_profile_identity_and_scope():
    profile = parse_profile(PAGE, "000338", "source")
    assert profile["main_business"] == "动力系统、商用车。"
    assert profile["products"] == "发动机"
    assert "经营范围" not in profile
    with pytest.raises(ValueError):
        parse_profile(PAGE, "601899", "source")
    with pytest.raises(ValueError):
        parse_profile(PAGE.replace("(000338)", "(601899)") + "推荐000338", "000338", "source")


def test_profile_cache_and_refresh_failure(tmp_path, monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=PAGE, raise_for_status=lambda: None)

    monkeypatch.setattr("data.business_profile.requests.get", get)
    cache = CacheManager(tmp_path / "cache.db")
    first = load_business_profile(cache, "000338")
    assert load_business_profile(cache, "000338") == first
    assert len(calls) == 1 and calls[0]["timeout"] == 8

    def fail(*args, **kwargs):
        raise TimeoutError("接口超时")

    monkeypatch.setattr("data.business_profile.requests.get", fail)
    assert load_business_profile(cache, "000338", refresh=True)["status"] == "unavailable"

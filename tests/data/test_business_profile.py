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


@pytest.mark.parametrize(
    ("market", "symbol", "row", "expected"),
    [
        ("hk", "00700.HK", {"SECUCODE": "00700.HK", "MAIN_BUSINESS": " 主营  介绍 ", "ORG_PROFILE": "备用"}, "主营 介绍"),
        ("hk", "00700.HK", {"SECUCODE": "00700.HK", "MAIN_BUSINESS": " ", "ORG_PROFILE": "备用"}, "备用"),
        ("us", "AAPL", {"SECUCODE": "AAPL.O", "SECURITY_CODE": "AAPL", "MAIN_BUSINESS": "忽略", "ORG_PROFILE": "公司资料"}, "公司资料"),
    ],
)
def test_f10_business_fields(market, symbol, row, expected):
    from data.business_profile import parse_f10_profile

    row = {**row, "ORG_NAME": "公司"}
    profile = parse_f10_profile([row], symbol, market, "F10")
    assert profile["main_business"] == expected
    assert profile["source_label"] == "东财 F10 公司资料"
    with pytest.raises(ValueError):
        parse_f10_profile([row, row], symbol, market, "F10")
    with pytest.raises(ValueError):
        parse_f10_profile([row], "WRONG", market, "F10")


def test_f10_missing_and_overlong_are_unavailable(tmp_path, monkeypatch):
    cache = CacheManager(tmp_path / "cache.db")
    row = {"SECURITY_CODE": "AAPL", "ORG_NAME": "公司", "ORG_PROFILE": ""}
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "success": True, "result": {"data": [row]}})

    monkeypatch.setattr("data.business_profile.requests.get", get)
    assert load_business_profile(cache, "AAPL", "us")["status"] == "unavailable"
    row["ORG_PROFILE"] = "公司" * 1001
    assert load_business_profile(cache, "AAPL", "us", refresh=True)["status"] == "unavailable"
    row["ORG_PROFILE"] = "公司介绍"
    result = load_business_profile(cache, "AAPL", "us", refresh=True)
    assert result == load_business_profile(cache, "AAPL", "us")
    assert result["main_business"] == "公司介绍"
    assert len(calls) == 3
    assert calls[-1]["params"]["filter"] == '(SECURITY_CODE="AAPL")'

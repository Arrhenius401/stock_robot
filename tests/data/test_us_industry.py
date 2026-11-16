"""美股行业身份与未核实行情的关闭边界。"""
import pytest

from data.us_industry import filter_members, us_industry_result


def row(code="MSFT", suffix="O", industry="系统软件", name="微软"):
    return {"SECURITY_CODE": code, "SECUCODE": f"{code}.{suffix}",
            "BELONG_INDUSTRY": industry, "ORG_NAME": name}


def test_members_exclude_self_foreign_invalid_and_other_industry():
    rows = [row(), row("AAPL"), row("FOREIGN", "F"), row("EMPTY", name=" "),
            row("OTHER", industry="其他"), row("BRK_B"), row("MSFT", "N")]
    assert filter_members(rows, "系统软件", "AAPL") == {"MSFT": "微软"}


def test_unverified_quotes_never_become_comparison(mocker):
    mocker.patch("data.us_industry._profiles", side_effect=[[row("AAPL")], [row()]])
    mocker.patch("data.us_industry._quotes", return_value=({}, []))
    result = us_industry_result("AAPL")
    assert result.value is not None
    assert result.record.result_state == "partial"
    assert not result.value.comparison_ready
    assert result.value.comparison_snapshot["revenue"] is None
    assert result.value.comparison_snapshot["attempts"][-1]["status"] == "unverified"
    assert result.value._target_pe is None


@pytest.mark.parametrize("profile", [row("OTHER"), row("AAPL", "F"), row("AAPL", name="")])
def test_wrong_profile_fails_closed(mocker, profile):
    mocker.patch("data.us_industry._profiles", return_value=[profile])
    mocker.patch("data.us_industry._quotes", return_value=({}, []))
    result = us_industry_result("AAPL")
    assert result.value is None
    assert result.record.result_state == "failed"


def test_berkshire_identity_alias(mocker):
    fetch = mocker.patch("data.us_industry._profiles", side_effect=[[row("BRK_B", "N")], []])
    mocker.patch("data.us_industry._quotes", return_value=({}, []))
    result = us_industry_result("BRK.B")
    assert result.value is not None
    assert result.value.symbol == "BRK.B"
    assert fetch.call_args_list[0].args == ('(SECURITY_CODE="BRK_B")',)


def test_profiles_pages_and_timeout(mocker):
    from data.us_industry import _profiles
    responses = [mocker.Mock(), mocker.Mock()]
    for response in responses:
        response.json.return_value = {"success": True, "result": {"pages": 2, "data": [row()]}}
    get = mocker.patch("data.us_industry.requests.get", side_effect=responses)
    assert len(_profiles("x")) == 2
    assert [call.kwargs["params"]["pageNumber"] for call in get.call_args_list] == [1, 2]
    assert all(call.kwargs["timeout"] == 10 for call in get.call_args_list)


@pytest.mark.parametrize("data", [{"diff": []}, {"diff": [{"f12": "WRONG"}]}, None])
def test_batch_missing_or_wrong_identity_falls_back(mocker, data):
    from data.us_industry import _quotes
    response = mocker.Mock()
    response.json.return_value = {"data": data}
    mocker.patch("data.us_industry.requests.get", return_value=response)
    single = mocker.patch("data.us_industry._single_quote", return_value={"symbol": "AAPL", "basis_verified": False})
    quotes, attempts = _quotes(["105.AAPL"])
    single.assert_called_once_with("105.AAPL")
    assert quotes["AAPL"]["basis_verified"] is False
    assert attempts


def test_cache_refresh_and_symbol_isolation(mocker, tmp_path):
    from data.cache import CacheManager
    cache = CacheManager(tmp_path / "cache.db")
    profiles = mocker.patch("data.us_industry._profiles", side_effect=lambda expression: [row("AAPL")] if "SECURITY_CODE" in expression else [row()])
    quotes = mocker.patch("data.us_industry._quotes", return_value=({"AAPL": {"basis_verified": False}}, []))
    us_industry_result("AAPL", cache=cache)
    us_industry_result("AAPL", cache=cache)
    assert profiles.call_count == 2
    assert quotes.call_count == 1
    us_industry_result("AAPL", cache=cache, refresh=True)
    assert profiles.call_count == 4
    assert quotes.call_count == 2
    assert us_industry_result("MSFT", cache=cache).value is None


@pytest.mark.parametrize("payload", [None, [], 1])
def test_non_object_quote_responses_are_closed(mocker, payload):
    from data.us_industry import _quotes, _single_quote
    response = mocker.Mock()
    response.json.return_value = payload
    mocker.patch("data.us_industry.requests.get", return_value=response)
    with pytest.raises(TypeError):
        _single_quote("105.AAPL")
    quotes, attempts = _quotes(["105.AAPL"])
    assert quotes == {}
    assert all(item["status"] == "failed" for item in attempts)


def test_cache_keeps_original_quote_time(mocker, tmp_path):
    from data.cache import CacheManager
    cache = CacheManager(tmp_path / "cache.db")
    mocker.patch("data.us_industry._profiles", side_effect=lambda expression: [row("AAPL")] if "SECURITY_CODE" in expression else [row()])
    mocker.patch("data.us_industry._quotes", return_value=({"AAPL": {"basis_verified": False}}, []))
    live = us_industry_result("AAPL", cache=cache)
    cached = us_industry_result("AAPL", cache=cache)
    assert live.value is not None and cached.value is not None
    assert live.record.collected_at == cached.record.collected_at
    assert live.value.comparison_snapshot["as_of"] == cached.value.comparison_snapshot["as_of"]
    assert cached.record.cache_status == "cache"


def test_corrupt_valid_json_cache_fails_closed(mocker, tmp_path):
    from datetime import datetime

    from data.cache import CacheManager
    cache = CacheManager(tmp_path / "cache.db")
    expression = '(SECURITY_CODE="AAPL")'
    cache.put("us_industry_profile", f"us_industry::v1::AAPL::{expression}", datetime.now().astimezone().date().isoformat(), "null")
    assert us_industry_result("AAPL", cache=cache).value is None

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
    result = us_industry_result("AAPL")
    assert result.value is None
    assert result.record.result_state == "failed"


def test_berkshire_identity_alias(mocker):
    fetch = mocker.patch("data.us_industry._profiles", side_effect=[[row("BRK_B", "N")], []])
    result = us_industry_result("BRK.B")
    assert result.value is not None
    assert result.value.symbol == "BRK.B"
    assert fetch.call_args_list[0].args == ('(SECURITY_CODE="BRK_B")',)

from http.client import RemoteDisconnected

from utils.symbols import normalize_symbol, resolve_name, validate_symbol


class TestNormalizeSymbol:
    def test_pads_to_six_digits(self):
        assert normalize_symbol("1") == "000001"
        assert normalize_symbol("000001") == "000001"

    def test_strips_prefixes(self):
        assert normalize_symbol("sh600036") == "600036"
        assert normalize_symbol("sz000001") == "000001"

    def test_handles_already_normalized(self):
        assert normalize_symbol("600036") == "600036"


class TestValidateSymbol:
    def test_valid_shanghai(self):
        assert validate_symbol("600036") is True

    def test_valid_shanghai_with_prefix(self):
        assert validate_symbol("sh600036") is True

    def test_valid_shenzhen(self):
        assert validate_symbol("000001") is True

    def test_valid_shenzhen_with_prefix(self):
        assert validate_symbol("sz000001") is True

    def test_valid_gem(self):
        assert validate_symbol("300750") is True

    def test_invalid_too_short(self):
        assert validate_symbol("123") is False

    def test_invalid_non_numeric(self):
        assert validate_symbol("abcdef") is False

    def test_invalid_starting_digit(self):
        assert validate_symbol("900001") is False

    def test_invalid_prefix_mismatch_sh_on_sz_code(self):
        """sh 前缀不能用于深交所代码"""
        assert validate_symbol("sh000001") is False

    def test_invalid_prefix_mismatch_sz_on_sh_code(self):
        """sz 前缀不能用于上交所代码"""
        assert validate_symbol("sz600036") is False


def test_resolve_name_retries_on_network_error(mocker):
    mocker.patch("utils.retry.time.sleep")
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RemoteDisconnected("boom")
        import pandas as pd
        return pd.DataFrame([
            {"code": "000001", "name": "平安银行"},
            {"code": "600036", "name": "招商银行"},
        ])

    mocker.patch("akshare.stock_info_a_code_name", side_effect=flaky)
    name = resolve_name("000001")
    assert calls["n"] == 3
    assert name == "平安银行"

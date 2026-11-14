from http.client import RemoteDisconnected

from data.collection_state import ValueResult, make_record
from utils.symbols import (
    is_us_symbol,
    normalize_symbol,
    normalize_us_symbol,
    resolve_name,
    resolve_name_result,
    symbol_market,
    validate_analyze_symbol,
    validate_symbol,
)


class TestNormalizeSymbol:
    def test_pads_to_six_digits(self):
        assert normalize_symbol("1") == "000001"
        assert normalize_symbol("000001") == "000001"

    def test_strips_prefixes(self):
        assert normalize_symbol("sh600036") == "600036"
        assert normalize_symbol("sz000001") == "000001"

    def test_handles_already_normalized(self):
        assert normalize_symbol("600036") == "600036"

    def test_normalizes_hk_symbol(self):
        assert normalize_symbol("00700") == "00700.HK"
        assert normalize_symbol("00700.HK") == "00700.HK"
        assert normalize_symbol("hk00700") == "00700.HK"


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

    def test_valid_hk_symbols(self):
        assert validate_symbol("00700") is True
        assert validate_symbol("00700.HK") is True
        assert validate_symbol("hk00700") is True


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


class TestUsSymbol:
    def test_accepts_common_forms(self):
        assert is_us_symbol("AAPL") is True
        assert is_us_symbol("aapl") is True
        assert is_us_symbol("AAPL.US") is True
        assert is_us_symbol("BRK.B") is True
        assert is_us_symbol("BRK-B") is True

    def test_rejects_a_share_and_hk(self):
        assert is_us_symbol("600519") is False
        assert is_us_symbol("000001") is False
        assert is_us_symbol("sh600036") is False
        assert is_us_symbol("00700") is False
        assert is_us_symbol("00700.HK") is False

    def test_rejects_empty_and_pure_digits(self):
        assert is_us_symbol("") is False
        assert is_us_symbol("12345") is False
        assert is_us_symbol("123456") is False

    def test_rejects_malformed(self):
        assert is_us_symbol("A_B") is False
        assert is_us_symbol("AAPL US") is False
        assert is_us_symbol(".AAPL") is False
        assert is_us_symbol("ABCDEFGHIJKLM") is False

    def test_normalize_us(self):
        assert normalize_us_symbol("aapl") == "AAPL"
        assert normalize_us_symbol("aapl.us") == "AAPL"
        assert normalize_us_symbol("BRK.B") == "BRK.B"
        assert normalize_us_symbol("BRK-B") == "BRK.B"
        assert normalize_us_symbol("BF-B") == "BF.B"

    def test_normalize_symbol_us(self):
        assert normalize_symbol("aapl") == "AAPL"
        assert normalize_symbol("AAPL.US") == "AAPL"
        assert normalize_symbol("BRK-B") == "BRK.B"
        assert normalize_symbol("00700") == "00700.HK"
        assert normalize_symbol("600519") == "600519"

    def test_symbol_market(self):
        assert symbol_market("AAPL") == "us"
        assert symbol_market("BRK.B") == "us"
        assert symbol_market("00700.HK") == "hk"
        assert symbol_market("600519") == "a-shares"

    def test_validate_analyze_symbol(self):
        assert validate_analyze_symbol("AAPL") is True
        assert validate_analyze_symbol("600519") is True
        assert validate_analyze_symbol("00700.HK") is True
        assert validate_analyze_symbol("") is False
        assert validate_analyze_symbol("AAPL US") is False
        # 旧入口保持 A 股/港股语义，回测等不会误收美股。
        assert validate_symbol("AAPL") is False


def test_resolve_name_result_us_uses_normalized_ticker(mocker):
    called = {}

    def fake_name(ticker):
        called["ticker"] = ticker
        return ValueResult("微软", make_record("新浪美股快照", "complete"))

    mocker.patch("data.us_stock.us_name_result", side_effect=fake_name)
    result = resolve_name_result("aapl.us")
    assert called["ticker"] == "AAPL"
    assert result.value == "微软"

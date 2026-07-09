import pytest
from utils.numbers import parse_cn_number


class TestParseCnNumber:
    def test_yi_unit(self):
        assert parse_cn_number("3.54亿") == pytest.approx(3.54e8)

    def test_wan_unit(self):
        assert parse_cn_number("7217.13万") == pytest.approx(7.21713e7)

    def test_wanyi_unit(self):
        assert parse_cn_number("1.2万亿") == pytest.approx(1.2e12)

    def test_negative(self):
        assert parse_cn_number("-3.54亿") == pytest.approx(-3.54e8)

    def test_plain_number_string(self):
        assert parse_cn_number("123.45") == pytest.approx(123.45)

    def test_thousands_separator(self):
        assert parse_cn_number("1,234.5") == pytest.approx(1234.5)

    def test_numeric_input_passthrough(self):
        assert parse_cn_number(100) == 100.0
        assert parse_cn_number(4.5e10) == pytest.approx(4.5e10)

    def test_dash_and_empty_and_none_return_none(self):
        assert parse_cn_number("-") is None
        assert parse_cn_number("--") is None
        assert parse_cn_number("") is None
        assert parse_cn_number(None) is None

    def test_garbage_returns_none(self):
        assert parse_cn_number("abc") is None

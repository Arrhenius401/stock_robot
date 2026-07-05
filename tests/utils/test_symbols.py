from src.utils.symbols import normalize_symbol, validate_symbol, resolve_name


class TestNormalizeSymbol:
    def test_pads_to_six_digits(self):
        assert normalize_symbol("1") == "000001"
        assert normalize_symbol("000001") == "000001"

    def test_strips_prefixes(self):
        assert normalize_symbol("sh000001") == "000001"
        assert normalize_symbol("sz000001") == "000001"

    def test_handles_already_normalized(self):
        assert normalize_symbol("600036") == "600036"


class TestValidateSymbol:
    def test_valid_shanghai(self):
        assert validate_symbol("600036") is True

    def test_valid_shenzhen(self):
        assert validate_symbol("000001") is True

    def test_valid_gem(self):
        assert validate_symbol("300750") is True

    def test_invalid_too_short(self):
        assert validate_symbol("123") is False

    def test_invalid_non_numeric(self):
        assert validate_symbol("abcdef") is False

    def test_invalid_starting_digit(self):
        assert validate_symbol("900001") is False

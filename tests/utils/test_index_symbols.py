"""指数代码工具测试"""
from src.data.index_mapping import IndexMapping
from src.utils.symbols import normalize_index_symbol, validate_index_symbol


class TestValidateIndexSymbol:
    def test_valid_broad_index(self):
        assert validate_index_symbol("000300") is True
        assert validate_index_symbol("sh000001") is True

    def test_valid_sector_index(self):
        # 行业板块代码以 8 开头
        assert validate_index_symbol("801010") is True

    def test_invalid_symbol_too_short(self):
        assert validate_index_symbol("123") is False

    def test_normalize_index_symbol(self):
        assert normalize_index_symbol("sh000300") == "000300"
        assert normalize_index_symbol("000300") == "000300"


class TestIndexMapping:
    def test_lookup_broad_index(self):
        mapping = IndexMapping()
        entry = mapping.lookup("000300")
        assert entry is not None
        assert entry.index_style == "broad"
        assert entry.name == "沪深300"

    def test_lookup_unknown_returns_none(self):
        mapping = IndexMapping()
        entry = mapping.lookup("999999")
        assert entry is None

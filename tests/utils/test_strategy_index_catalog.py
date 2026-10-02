"""策略指数目录与ETF跟踪关系回归测试。"""
import pytest

from data.index_mapping import ETFIndexMapping, IndexMapping
from utils.symbols import normalize_index_symbol, validate_index_symbol


def test_alphanumeric_csi_code_is_strict():
    assert validate_index_symbol("h30269")
    assert normalize_index_symbol("h30269") == "H30269"
    assert not validate_index_symbol("H3026")
    assert not validate_index_symbol("ABC123456")


def test_catalog_separates_low_volatility_indices():
    mapping = IndexMapping()
    for name, code in [("沪深300红利低波动", "930740"), ("中证红利低波动100", "930955"),
                       ("h30269", "H30269"), ("国证自由现金流", "980092"), ("中证现金流", "932365")]:
        entry = mapping.resolve(name)
        assert entry is not None
        assert entry.symbol == code
    cashflow = mapping.lookup("980092")
    assert cashflow is not None
    assert cashflow.provider == "cni"
    assert len(mapping.search("红利")) >= 5


def test_ambiguous_name_is_not_arbitrarily_resolved():
    with pytest.raises(ValueError, match="多个指数"):
        IndexMapping().resolve("自由现金流")


def test_legacy_csv_remains_loadable(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text("symbol,name,index_style,market\n000300,沪深300,broad,a-shares\n", encoding="utf-8")
    entry = IndexMapping(path).resolve("000300")
    assert entry is not None
    assert entry.name == "沪深300"
    assert entry.strategy_kind == ""


def test_515300_tracks_300_dividend_low_volatility():
    etf = ETFIndexMapping().lookup("515300")
    assert etf is not None
    assert etf.index_symbol == "930740"
    assert "沪深300" in etf.name
    assert etf.index_symbol != "930955"


def test_strategy_subscription_accepts_catalog_type():
    from push.models import SubscriptionSymbol

    item = SubscriptionSymbol(symbol="H30269", kind="index", index_style="strategy")
    assert item.index_style == "strategy"

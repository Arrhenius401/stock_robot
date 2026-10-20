"""P0 数值有效性与展示口径，保留计算原值。"""
import math

import pytest

from report.formatter import format_metric_value, format_metrics
from utils.numbers import is_finite_number, is_positive_finite, is_valid_percentile


@pytest.mark.parametrize("value", [None, True, "1", math.nan, math.inf, -math.inf])
def test_invalid_numeric_input(value):
    assert not is_finite_number(value)
    assert not is_positive_finite(value)
    assert not is_valid_percentile(value)


def test_numeric_boundaries():
    assert is_finite_number(0)
    assert not is_positive_finite(0)
    assert not is_positive_finite(-1)
    assert is_positive_finite(0.01)
    assert is_valid_percentile(0) and is_valid_percentile(100)
    assert not is_valid_percentile(-1) and not is_valid_percentile(101)


DISPLAY_CASES = [
    ("revenue", 3000000, "300.00 万"),
    ("revenue", 2623000000, "26.23 亿"),
    ("net_profit", -382000000, "-3.82 亿"),
    ("revenue", 3500, "3,500.00"),
    ("revenue", 10000, "1.00 万"),
    ("market_cap", 100000000, "1.00 亿"),
    ("north_bound", 5.2, "5.20 亿"),
    ("north_bound", 0, "0.00 亿"),
    ("north_bound", 0.0001, "0.0001 亿"),
    ("north_bound", -0.0001, "-0.0001 亿"),
    ("north_bound", 0.000001, "1e-06 亿"),
    ("north_bound", -0.00000001, "-1e-08 亿"),
    ("main_net_inflow", 0.0001, "0.0001（原始单位未记录）"),
    ("main_net_inflow", 3000000, "3,000,000.00（原始单位未记录）"),
    ("roe", -0.0956, "-9.56%"),
    ("gross_margin", 0.161, "16.10%"),
    ("change_pct", 0, "0.00%"),
    ("pe_percentile", 12.34, "12.34%"),
    ("target_pe_premium", -12.34, "-12.34%"),
    ("dividend_yield", 3.5, "3.50%"),
    ("pb", 1.2, "1.20"),
    ("score", 1.5999999999999996, "1.6"),
    ("latest_close", 0.0012, "0.0012"),
    ("ma_60", 443.2266666666667, "443.23"),
    ("macd_dif", -5.305251657462463, "-5.305"),
    ("symbol", "00700.HK", "00700.HK"),
    ("peer_count", 125, 125),
    ("new_vendor_metric", 3.123456, 3.123456),
    ("roe", None, "暂无数据"),
    ("roe", math.nan, "数据异常"),
]


@pytest.mark.parametrize("key,value,expected", DISPLAY_CASES)
def test_display_contract(key, value, expected):
    assert format_metric_value(key, value) == expected


def test_nested_display_does_not_modify_raw_values():
    raw = {"roe_trend": [{"quarter": "2026-06-30", "roe": -0.0956}], "pe_ttm": -32.03}
    result = format_metrics(raw)
    assert result["roe_trend"][0]["roe"] == "-9.56%"
    assert result["pe_ttm"] == "-32.03（不适用于高低估判断）"
    assert raw["roe_trend"][0]["roe"] == -0.0956
    assert raw["pe_ttm"] == -32.03

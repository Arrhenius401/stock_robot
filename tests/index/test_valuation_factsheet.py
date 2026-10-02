"""官方指数单张的口径、日期和缺失字段回归。"""
from datetime import date

import pytest

from index.valuation_factsheet import parse_factsheet_text


def test_cni_actual_excerpt_keeps_unspecified_pe_basis():
    text = "指数单张\n指数全称 国证自由现金流指数 指数代码 980092\n版权所有 2026 年 09 月\n估值数据\nPE PB ROE\n13.5 1.27 9.31\n十大权重"
    result = parse_factsheet_text(text, "980092", "cni", date(2026, 10, 2))
    assert result["as_of"] == date(2026, 9, 30)
    assert result["pe_snapshot"] == 13.5
    assert result["pb"] == 1.27
    assert "未声明TTM" in result["pe_basis"]


def test_csi_layout_only_reads_same_row_and_handles_leading_decimal():
    text = "2026年8月31日\n全称 指数代码\n沪深300红利低波动指数 930740\n滚动市盈率     8.95\n市净率    .78\n股息率   4.65%\n发布日期 2018年12月4日"
    result = parse_factsheet_text(text, "930740", "csi", date(2026, 10, 2))
    assert result["as_of"] == date(2026, 8, 31)
    assert result["pb"] == 0.78
    assert result["pe_snapshot"] == 8.95


def test_csi_missing_values_are_not_stolen_from_other_rows():
    text = "2026年8月31日\n指数代码 932365\n滚动市盈率 --\n市净率 --\n指数市值 2096"
    result = parse_factsheet_text(text, "932365", "csi", date(2026, 10, 2))
    assert result["pb"] is None
    assert result["pe_snapshot"] is None


@pytest.mark.parametrize("text", [
    "2026年9月30日 指数代码 930955 市净率 0.8",
    "2026年11月30日 指数代码 930740 市净率 0.8",
    "2026年6月30日 指数代码 930740 市净率 0.8",
    "指数代码 930740 市净率 0.8",
])
def test_wrong_symbol_future_stale_or_undated_snapshot_rejected(text):
    with pytest.raises(ValueError):
        parse_factsheet_text(text, "930740", "csi", date(2026, 10, 2))


def test_actual_cni_layout_with_industry_table_in_same_row():
    text = "指数代码 980092\n2026 年 09 月\n估值数据\n     医药卫生                12               3.40%               PE            PB           ROE\n     公用事业                 4               1.20%               13.5         1.27          9.31\n十大权重"
    result = parse_factsheet_text(text, "980092", "cni", date(2026, 10, 2))
    assert result["pe_snapshot"] == 13.5
    assert result["pb"] == 1.27


def test_target_code_only_in_benchmark_does_not_match_factsheet_identity():
    text = "2026年8月31日\n指数代码 930955\n对比基准：沪深300红利低波动指数 930740\n滚动市盈率 99\n市净率 99"
    with pytest.raises(ValueError, match="指数代码"):
        parse_factsheet_text(text, "930740", "csi", date(2026, 10, 2))

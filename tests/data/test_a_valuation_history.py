import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from data.a_valuation_history import load_history, parse_history


def sample_frame():
    data = json.loads((Path(__file__).parents[1] / "resources/valuation/eastmoney-600519-value-history.json").read_text(encoding="utf-8"))
    return pd.DataFrame(data["rows"])


def test_parse_maps_columns_and_sorts():
    rows = parse_history(sample_frame(), today=date(2026, 11, 24))
    assert rows[0].trade_date == date(2018, 1, 2)
    assert rows[-1].trade_date == date(2026, 11, 24)
    assert rows[-1].pe_ttm == pytest.approx(22.21400387)
    assert rows[-1].pb == pytest.approx(6.7296727)
    assert rows[-1].ps_ttm == pytest.approx(10.46309676)
    assert rows[-1].total_shares == pytest.approx(1250081601)


def test_parse_drops_future_dates_and_conflicting_duplicates():
    frame = sample_frame()
    conflict = frame.tail(1).copy()
    conflict["PE(TTM)"] = 999
    rows = parse_history(pd.concat([frame, conflict]), today=date(2026, 11, 24))
    assert all(row.trade_date != date(2026, 11, 24) for row in rows)


def test_load_history_reports_invalid_symbol_without_network(mocker):
    mocker.patch("data.a_valuation_history.ak.stock_value_em", side_effect=AssertionError("不应联网"))
    result = load_history("ABC")
    assert result.value is None
    assert result.record.result_state == "failed"
    assert "六位" in result.record.error_message


def test_failed_refresh_retains_source_success_and_original_date(mocker):
    mocker.patch("data.a_valuation_history.ak.stock_value_em", return_value=sample_frame())
    first = load_history("600519")
    mocker.patch("data.a_valuation_history.ak.stock_value_em", side_effect=ConnectionError("东财失败"))
    fallback = load_history("600519", refresh=True)
    assert fallback.value == first.value
    assert fallback.record.cache_status == "stale"
    assert fallback.record.collected_at == first.record.collected_at

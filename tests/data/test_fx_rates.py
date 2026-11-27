"""合成日频样本验证日期、平均值和失败边界，不依赖在线汇率。"""
from datetime import date

import pytest

from data import fx_rates


@pytest.mark.parametrize("currency,rate", [("HKD", 0.9), ("USD", 7.0)])
def test_single_day_and_success_cache(mocker, currency, rate):
    request = mocker.patch("data.fx_rates._request", return_value={
        "amount": 1.0, "base": currency, "date": "2025-01-03", "rates": {"CNY": rate}})
    result = fx_rates.rate_on(currency, day=date(2025, 1, 3))
    assert result.rate == rate and result.dates == (date(2025, 1, 3),)
    assert result.source == "ECB reference rates via Frankfurter"
    assert fx_rates.rate_on(currency, day=date(2025, 1, 3)) == result
    request.assert_called_once()


def test_average_uses_only_actual_dates_in_period(mocker):
    mocker.patch("data.fx_rates._request", return_value={"amount": 1.0, "base": "HKD", "rates": {
        "2025-01-02": {"CNY": 0.8}, "2025-01-03": {"CNY": 1.0}, "2024-12-31": {"CNY": 9}}})
    result = fx_rates.average_rate("HKD", date(2025, 1, 1), date(2025, 1, 5))
    assert result.rate == pytest.approx(0.9)
    assert result.dates == (date(2025, 1, 2), date(2025, 1, 3))


def test_weekend_keeps_actual_reference_date(mocker):
    mocker.patch("data.fx_rates._request", return_value={
        "amount": 1.0, "base": "HKD", "date": "2025-01-03", "rates": {"CNY": 0.9}})
    result = fx_rates.rate_on("HKD", day=date(2025, 1, 5))
    assert result.dates == (date(2025, 1, 3),)
    assert "2025-01-03" in result.note() and "2025-01-05" not in result.note()


@pytest.mark.parametrize("payload", [{}, {"amount": 1.0, "base": "HKD", "rates": {}},
    {"amount": 1.0, "base": "USD", "date": "2025-01-03", "rates": {"CNY": 7.0}},
    {"amount": 1.0, "base": "HKD", "date": "2025-01-06", "rates": {"CNY": 0.9}}])
def test_no_data_or_wrong_pair_or_future_date(mocker, payload):
    mocker.patch("data.fx_rates._request", return_value=payload)
    result = fx_rates.rate_on("HKD", day=date(2025, 1, 5))
    assert result.rate is None and result.reason
    assert "汇率未核实" in result.note()


def test_network_failure_is_explained_not_cached_as_success():
    result = fx_rates.average_rate("USD", date(2025, 1, 1), date(2025, 1, 5))
    assert result.rate is None and "ConnectionError" in result.reason


def test_partial_period_coverage_is_rejected(mocker):
    mocker.patch("data.fx_rates._request", return_value={"amount": 1.0, "base": "USD", "rates": {
        "2025-01-20": {"CNY": 7.0}}})
    result = fx_rates.average_rate("USD", date(2025, 1, 1), date(2025, 1, 31))
    assert result.rate is None and "覆盖" in result.reason


def test_internal_gap_does_not_become_period_average(mocker):
    mocker.patch("data.fx_rates._request", return_value={"amount": 1.0, "base": "USD", "rates": {
        "2025-01-02": {"CNY": 7.0}, "2025-12-31": {"CNY": 6.0}}})
    result = fx_rates.average_rate("USD", date(2025, 1, 1), date(2025, 12, 31))
    assert result.rate is None and "覆盖" in result.reason


def test_cny_passthrough_needs_no_source():
    assert fx_rates.rate_on("CNY", day=date(2025, 1, 5)).rate == 1
    assert fx_rates.average_rate("CNY", date(2025, 1, 1), date(2025, 1, 5)).rate == 1

"""统一财务指标：披露优先、精度和比较口径。"""
from datetime import date
from typing import Literal

import pytest

from data.financial_metrics import resolve_financial_metric
from data.schemas import AnalysisContext, FinancialData


def row(year, amount, precision: Literal["source_native", "rounded", "estimated", "unknown"] = "source_native"):
    ending = date(year, 9, 30)
    return FinancialData(symbol="688836", fiscal_quarter=ending, net_profit=amount,
        period_start=date(year, 1, 1), period_basis="cumulative", report_currency="CNY",
        reporting_basis="合并累计", financial_source="测试财报", precision_version=1,
        field_precision={"net_profit": precision},
        field_positions={"net_profit": {"definition": "parent_net_profit", "unit": "currency",
            "currency": "CNY", "start": f"{year}-01-01", "basis": "cumulative",
            "scope": "consolidated", "accounting": "CAS", "revision": "同一重述包"}})


def ctx(*rows):
    return AnalysisContext(symbol="688836", name="目标", financial_data=list(rows))


def test_native_amount_fallback_keeps_full_precision():
    result = resolve_financial_metric(ctx(row(2025, 104358516.63), row(2026, 386691923.61)),
                                     "profit_growth_yoy")
    assert result.value is not None
    assert result.value.value == pytest.approx(2.70541797734635)
    assert result.value.method == "calculated"


@pytest.mark.parametrize("precision", ["rounded", "estimated", "unknown"])
def test_lossy_amounts_never_produce_precise_yoy(precision):
    result = resolve_financial_metric(ctx(row(2025, 1.04e8, precision), row(2026, 3.87e8)),
                                     "profit_growth_yoy")
    assert result.value is None
    assert result.record.error_kind == "insufficient_evidence"


def test_reported_value_has_priority_and_does_not_need_previous_amount():
    latest = row(2026, 3.87e8, "rounded")
    latest.reported_metrics = {"profit_growth_yoy": 2.70541798}
    latest.field_positions["profit_growth_yoy"] = {
        **latest.field_positions["net_profit"], "definition": "profit_growth_yoy", "unit": "ratio"}
    latest.field_precision["profit_growth_yoy"] = "source_native"
    result = resolve_financial_metric(ctx(latest), "profit_growth_yoy")
    assert result.value is not None and result.value.method == "reported"
    assert result.value.value == 2.70541798


@pytest.mark.parametrize("key,value", [
    ("currency", "USD"), ("revision", ""), ("revision", "不同版本"),
    ("scope", "standalone"), ("accounting", "IFRS"), ("definition", "total_net_profit")])
def test_incompatible_pair_is_rejected(key, value):
    before, after = row(2025, 100), row(2026, 200)
    before.field_positions["net_profit"][key] = value
    assert resolve_financial_metric(ctx(before, after), "profit_growth_yoy").value is None


@pytest.mark.parametrize("amount", [0, -100])
def test_nonpositive_base_has_no_ordinary_growth(amount):
    result = resolve_financial_metric(ctx(row(2025, amount), row(2026, 100)), "profit_growth_yoy")
    assert result.value is None and result.record.result_state == "not_applicable"


def test_duplicate_period_conflict_and_unknown_legacy_precision():
    assert resolve_financial_metric(ctx(row(2026, 100), row(2026, 101)), "profit_growth_yoy").value is None
    legacy = FinancialData(symbol="688836", fiscal_quarter=date(2026, 9, 30), net_profit=3.87e8)
    restored = FinancialData.model_validate_json(legacy.model_dump_json())
    assert restored.field_precision == {} and restored.precision_version == 0


@pytest.mark.parametrize("historical", ["negative_other_currency", "duplicate"])
def test_unrelated_history_cannot_override_valid_disclosure(historical):
    latest = row(2026, 200)
    latest.reported_metrics["profit_growth_yoy"] = .25
    latest.field_positions["profit_growth_yoy"] = {
        **latest.field_positions["net_profit"], "definition": "profit_growth_yoy", "unit": "ratio"}
    latest.field_precision["profit_growth_yoy"] = "source_native"
    before = row(2025, -100)
    before.field_positions["net_profit"]["currency"] = "USD"
    records = [before, latest] if historical == "negative_other_currency" else [before, before.model_copy(deep=True), latest]
    result = resolve_financial_metric(ctx(*records), "profit_growth_yoy")
    assert result.value is not None and result.value.method == "reported"
    assert result.value.value == .25


def test_invalid_disclosure_can_fall_back_to_compatible_native_amounts():
    before, latest = row(2025, 100), row(2026, 120)
    latest.reported_metrics["profit_growth_yoy"] = 999
    latest.field_precision["profit_growth_yoy"] = "source_native"
    latest.field_positions["profit_growth_yoy"] = {
        **latest.field_positions["net_profit"], "definition": "profit_growth_yoy", "unit": "percent"}
    result = resolve_financial_metric(ctx(before, latest), "profit_growth_yoy")
    assert result.value is not None and result.value.value == .2
    assert result.value.method == "calculated"


def test_us_specialized_ttm_cannot_bypass_unknown_precision():
    from data.us_stock import us_ttm

    rows = [FinancialData(symbol="TEST", fiscal_quarter=date(2025, month, 30),
                          revenue=10, net_profit=1) for month in (3, 6, 9, 12)]
    assert us_ttm(rows)["revenue"] is None
    assert us_ttm(rows)["net_profit"] is None


def test_us_old_precision_cache_requires_refresh(tmp_path):
    import json

    from core.pipeline import Pipeline
    from core.registry import Registry
    from utils.config import Config

    pipeline = Pipeline(Registry(), Config(tmp_path), llm_enabled=False)
    old = FinancialData(symbol="AAPL", fiscal_quarter=date(2026, 9, 30), revenue=100)
    raw = {**old.model_dump(mode="json"), "_us_financial_version": 1}
    pipeline._cache.put("financial", "AAPL", "latest", json.dumps([raw]))
    assert pipeline._get_cached("AAPL", "financial") is None

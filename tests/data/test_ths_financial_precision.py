"""2026-11-22 同花顺长表样本；仅裁取 688836 同期相关科目，不含公告核对。"""
import json
from pathlib import Path

import pandas as pd
import pytest

from data.a_financial_policy import annotate_selection
from data.financial_metrics import resolve_financial_metric
from data.financial_selection import FinancialPackage, FinancialSelection
from data.schemas import AnalysisContext, FinancialData
from data.ths_financial import fetch_ths_native


def sample(mocker):
    raw = json.loads((Path(__file__).parents[1] / "resources/data/ths-688836-financial-precision.json").read_text(encoding="utf-8"))
    for key, api in (("summary", "abstract"), ("balance", "debt"), ("cash", "cash")):
        mocker.patch(f"akshare.stock_financial_{api}_new_ths", return_value=pd.DataFrame(raw[key]))
    return raw


def test_native_amounts_source_rates_and_persistence(mocker):
    sample(mocker)
    rows = fetch_ths_native("688836")
    latest = rows[-1]
    assert latest.net_profit == 386691923.61
    assert latest.revenue == 1817290048.48
    assert latest.operating_cash_flow == 193707344.45
    assert latest.capital_expenditure == 101302479.81
    assert latest.free_cash_flow == pytest.approx(92404864.64)
    assert latest.gross_margin == pytest.approx(.540213)
    assert latest.net_margin == pytest.approx(.212785)
    assert rows[0].deducted_net_profit is None
    output = annotate_selection(FinancialSelection(package=FinancialPackage("同花顺财报", rows, {})))
    restored = [FinancialData.model_validate_json(row.model_dump_json()) for row in output]
    ctx = AnalysisContext(symbol="688836", name="目标", financial_data=restored)
    for metric, expected in (("profit_growth_yoy", 2.70541798),
                             ("revenue_growth_yoy", .55711212),
                             ("deducted_profit_growth_yoy", -.189592),
                             ("cash_flow_growth_yoy", -.54691756)):
        result = resolve_financial_metric(ctx, metric)
        assert result.value is not None and result.value.method == "reported"
        assert result.value.value == pytest.approx(expected)


def test_duplicate_subject_does_not_silently_win(mocker):
    raw = sample(mocker)
    data = [*raw["summary"], {**raw["summary"][0], "value": "999"}]
    mocker.patch("akshare.stock_financial_abstract_new_ths", return_value=pd.DataFrame(data))
    assert fetch_ths_native("688836") == []


def test_failed_native_table_falls_back_as_whole_package(mocker):
    sample(mocker)
    mocker.patch("akshare.stock_financial_cash_new_ths", side_effect=ConnectionError("新版现金流不可用"))
    from data.akshare import AkShareAdapter

    adapter = AkShareAdapter()
    legacy = mocker.patch.object(adapter, "_fetch_ths_legacy_financial", return_value=[])
    assert adapter._fetch_ths_financial("688836") == []
    legacy.assert_called_once_with("688836")


def test_table_and_trends_consume_the_same_disclosed_rate(mocker):
    sample(mocker)
    from analysis.financial import FinancialAnalyzer

    rows = fetch_ths_native("688836")
    result = FinancialAnalyzer().analyze(AnalysisContext(symbol="688836", name="目标", financial_data=rows))
    assert result.metrics["profit_growth_yoy"] == pytest.approx(2.70541798)
    assert result.metrics["deducted_profit_growth_yoy"] == pytest.approx(-.189592)
    assert any("18.96%" in text for text in result.metrics["operating_trends"])
    assert "272.12" not in str(result.metrics)


def test_ttm_ignores_unrelated_old_unknown_amounts():
    from datetime import date

    from data.financial_ttm import financial_ttm

    rows = [
        FinancialData(symbol="TEST", fiscal_quarter=date(2024, 3, 31)),
        FinancialData(symbol="TEST", fiscal_quarter=date(2025, 9, 30), net_profit=100,
                      field_precision={"net_profit": "source_native"}),
        FinancialData(symbol="TEST", fiscal_quarter=date(2025, 12, 31), net_profit=150,
                      field_precision={"net_profit": "source_native"}),
        FinancialData(symbol="TEST", fiscal_quarter=date(2026, 9, 30), net_profit=120,
                      field_precision={"net_profit": "source_native"}),
    ]
    assert financial_ttm(rows, "net_profit", "a-shares", require_precision=True) == 170
    rows[-1].field_precision["net_profit"] = "rounded"
    assert financial_ttm(rows, "net_profit", "a-shares", require_precision=True) is None


@pytest.mark.parametrize("symbol", ["688836", "00700.HK"])
def test_cache_version_does_not_upgrade_unknown_precision(symbol):
    import time
    from datetime import date

    from core.resilient_collection import ResilientCollector, Snapshot
    from data.cache import CacheEntry
    from data.collection_state import make_record

    row = FinancialData(symbol=symbol, fiscal_quarter=date(2026, 9, 30), revenue=100)
    snapshot = Snapshot([row], make_record("测试", "complete"),
                        CacheEntry("[]", time.time(), 3600, None))
    assert not ResilientCollector._fresh(snapshot)
    row.precision_version = 1
    assert row.field_precision == {}
    assert ResilientCollector._fresh(snapshot)


def test_roe_rejects_rounded_equity(mocker):
    sample(mocker)
    from analysis.financial import FinancialAnalyzer

    latest = fetch_ths_native("688836")[-1]
    latest.fiscal_quarter = latest.fiscal_quarter.replace(month=12, day=31, year=2025)
    latest.period_start = latest.fiscal_quarter.replace(month=1, day=1)
    latest.common_equity = None
    latest.field_precision["total_equity"] = "rounded"
    result = FinancialAnalyzer().analyze(AnalysisContext(symbol="688836", name="目标", financial_data=[latest]))
    assert result.metrics["roe"] is None

"""A 股正式适配器选源及来源持久化契约。"""

from datetime import date

import pytest

from data.akshare import AkShareAdapter
from data.financial_selection import FinancialPackage, Position
from data.schemas import FinancialData


def financial_rows(source="同花顺财报"):
    rows = [FinancialData(
        symbol="600519", fiscal_quarter=period, revenue=100, net_profit=10,
        deducted_net_profit=9, total_assets=200, total_equity=80, roe=0.1,
        basic_eps=1, operating_cash_flow_per_share=2, operating_cash_flow=20,
        cash_flow_basis="estimated", period_start=date(period.year, 1, 1),
        period_basis="cumulative", reporting_basis="同花顺财务摘要-按报告期", report_currency="CNY",
    ) for period in (date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30))]
    for row in rows:
        row._data_source = source
    return rows


@pytest.fixture
def sources(monkeypatch):
    primary, sina, em = financial_rows(), financial_rows("新浪财报"), financial_rows("东方财富财报")
    calls = []

    def fetch(label, rows):
        calls.append(label)
        return FinancialPackage(label, rows, {})

    monkeypatch.setattr(AkShareAdapter, "_fetch_ths_financial", lambda *_a, **_kw: primary)
    monkeypatch.setattr("data.a_financial_sources.fetch_sina_financial", lambda _: fetch("新浪财报", sina))
    monkeypatch.setattr("data.a_financial_sources.fetch_eastmoney_financial", lambda _: fetch("东方财富财报", em))
    return primary, sina, em, calls


def test_complete_primary_preserves_all_numerical_facts_and_does_not_call_backup(sources):
    primary, _, _, calls = sources
    before = [row.model_dump() for row in primary]
    result = AkShareAdapter().fetch("600519", data_type="financial")
    assert calls == []
    for actual, expected in zip(result, before, strict=True):
        from data.financial_selection import NUMERIC_FIELDS

        assert {name: getattr(actual, name) for name in NUMERIC_FIELDS} == {name: expected[name] for name in NUMERIC_FIELDS}
        assert actual.cash_flow_basis == "estimated"
        assert actual.financial_source == "同花顺财报"
        assert actual.financial_selection_status == "complete"


def test_partial_primary_prefers_whole_sina_package(sources):
    primary, sina, _, calls = sources
    primary[-1].total_assets = None
    sina[-1].net_profit = 99
    result = AkShareAdapter().fetch("600519", data_type="financial")
    assert calls == ["新浪财报"]
    assert result[-1].net_profit == 99
    assert result[-1].financial_source == "新浪财报"
    assert "备用" in result[-1].financial_selection_note
    assert primary[-1].total_assets is None


def test_primary_error_calls_backup_and_keeps_safe_diagnostic(sources, monkeypatch):
    def failed(*_a, **_kw):
        raise TimeoutError("原始请求URL与密钥不得进入报告")

    monkeypatch.setattr(AkShareAdapter, "_fetch_ths_financial", failed)
    adapter = AkShareAdapter()
    result = adapter.fetch("600519", data_type="financial")
    assert result[-1].financial_source == "新浪财报"
    error = adapter.get_collection_error()
    assert error and "超时" in error.error_message
    assert "密钥" not in error.error_message


def test_two_partial_sources_choose_complete_eastmoney(sources):
    primary, sina, _, calls = sources
    primary[-1].total_assets = None
    sina[-1].total_equity = None
    result = AkShareAdapter().fetch("600519", data_type="financial")
    assert calls == ["新浪财报", "东方财富财报"]
    assert result[-1].financial_source == "东方财富财报"


def test_all_partial_without_version_proof_do_not_stitch(sources):
    primary, sina, em, calls = sources
    primary[-1].total_assets = None
    sina[-1].total_equity = None
    em[-1].roe = None
    result = AkShareAdapter().fetch("600519", data_type="financial")
    assert len(calls) == 2
    assert result[-1].total_assets is None
    assert result[-1].financial_selection_status == "partial"
    assert result[-1].field_sources["net_profit"] == "同花顺财报"


def test_older_complete_backup_does_not_hide_current_partial(sources):
    primary, sina, em, _ = sources
    primary[-1].total_assets = None
    sina.pop()
    em.pop()
    result = AkShareAdapter().fetch("600519", data_type="financial")
    assert result[-1].fiscal_quarter == date(2026, 6, 30)
    assert result[-1].total_assets is None


def test_negative_profit_and_zero_assets_do_not_count_as_missing(sources):
    primary, _, _, calls = sources
    primary[-1].net_profit = -10
    primary[-1].total_assets = 0
    primary[-1].operating_cash_flow = None
    primary[-1].cash_flow_basis = "unknown"
    result = AkShareAdapter().fetch("600519", data_type="financial")
    assert calls == []
    assert result[-1].net_profit == -10
    assert result[-1].total_assets == 0


def test_hk_still_uses_existing_path(monkeypatch):
    monkeypatch.setattr(AkShareAdapter, "_fetch_hk_financial", lambda *_: [])
    assert AkShareAdapter().fetch("00700.HK", data_type="financial", market="hk") == []


def test_proven_same_position_supplement_reaches_provenance(sources, monkeypatch):
    primary, sina, em, _ = sources
    primary.clear()
    sina[-1].total_assets = None
    em[-1].roe = None
    period = date(2026, 6, 30)
    position = Position(
        definition="总资产", unit="元", currency="CNY", start=None, basis="instant",
        scope="合并", accounting="CAS", revision="同一原始披露文档标识",
    )
    from dataclasses import replace

    positions = {period: {"total_assets": position, "total_equity": replace(position, definition="总权益")}}
    # 仅在测试中注入明确证据，不让生产解析器根据数值/公告日期猜版本。
    monkeypatch.setattr("data.a_financial_sources.fetch_sina_financial", lambda _: FinancialPackage(
        "新浪财报", sina, positions,
    ))
    monkeypatch.setattr("data.a_financial_sources.fetch_eastmoney_financial", lambda _: FinancialPackage(
        "东方财富财报", em, positions,
    ))
    result = AkShareAdapter().fetch("600519", data_type="financial")
    assert result[-1].total_assets == 200
    assert result[-1].financial_selection_status == "complete"
    assert result[-1].field_sources["total_assets"] == "东方财富财报"
    assert result[-1].field_positions["total_assets"]["revision"] == "同一原始披露文档标识"
    assert "非单一来源" in result[-1].financial_selection_note


def test_all_sources_empty_returns_no_financial_rows(sources):
    primary, sina, em, _ = sources
    primary.clear()
    sina.clear()
    em.clear()
    assert AkShareAdapter().fetch("600519", data_type="financial") == []


@pytest.mark.parametrize("field,dependency,value", [
    ("total_assets", "total_equity", 120), ("gross_margin", "revenue", 200),
])
def test_actual_scoring_dependencies_cannot_mix_conflicting_facts(field, dependency, value):
    from dataclasses import replace

    from data.a_financial_policy import DEPENDENCIES
    from data.financial_selection import select_financial

    primary, donor = financial_rows(), financial_rows("备用")
    setattr(primary[-1], field, None)
    setattr(donor[-1], field, 200 if field == "total_assets" else 0.5)
    setattr(donor[-1], dependency, value)
    period = primary[-1].fiscal_quarter
    position = Position(
        definition=field, unit="元", currency="CNY", start=None, basis="instant",
        scope="合并", accounting="CAS", revision="同一披露文档",
    )
    positions = {period: {field: position, dependency: replace(position, definition=dependency)}}
    packages = [FinancialPackage("主", primary, positions), FinancialPackage("备", donor, positions)]
    result = select_financial(
        [(p.source, lambda p=p: p) for p in packages], symbol="600519",
        required=frozenset({"common_equity"}), dependencies=DEPENDENCIES,
    )
    assert result.package is not None
    assert getattr(result.package.rows[-1], field) is None


def test_trailing_empty_period_is_removed_before_window_observation(sources):
    primary, _, _, calls = sources
    primary[-1].total_assets = None
    primary.append(FinancialData(symbol="600519", fiscal_quarter=date(2026, 9, 30)))
    result = AkShareAdapter().fetch("600519", data_type="financial")
    assert calls == ["新浪财报"]
    assert result[-1].fiscal_quarter == date(2026, 6, 30)
    assert result[-1].financial_selection_status == "complete"

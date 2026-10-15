"""两池风格命令的真实证据审核往返及版本失效保护。"""

import json
from datetime import UTC, date, datetime
from typing import Any

from click.testing import CliRunner

from radar.stocks.research_store import StockResearchStore
from stock_robot import stock_admission

AS_OF = date(2026, 10, 15)


def prepare(tmp_path):
    db = tmp_path / "style.db"
    store = StockResearchStore(db, clock=lambda: datetime(2026, 10, 15, 1, tzinfo=UTC))
    store.import_companies([{"symbol": "600900", "name": "合成公司"}], source_sha256="a" * 64,
                           source_label="合成测试", data_as_of=AS_OF)
    return store, db


def add(store, kind, payload, *, reviewed=True):
    company = store.get_company("600900")
    assert company is not None
    added = store.add_evidence("600900", kind=kind, payload=payload, source_url="https://example.com/synthetic.pdf",
                               disclosed_on=AS_OF, content_sha256="b" * 64, expected_revision=company["revision"])
    if reviewed:
        company = store.get_company("600900")
        assert company is not None
        store.save_review("600900", added["evidence_id"], expected_revision=company["revision"],
                          decision="confirmed", reviewer="合成测试", reason="验证风格分歧")


def cash_facts(store, *, reviewed=True):
    add(store, "admission_security", {"exchange": "SSE", "security_type": "ordinary_a", "issuer_type": "ordinary", "a_listed_on": "2010-01-01"})
    add(store, "admission_profitability", {"currency": "CNY", "scope": "company_parent", "annual": [
        {"year": year, "parent_profit_yuan": "100", "deducted_parent_profit_yuan": "90", "disclosed_on": f"{year+1}-04-01"}
        for year in (2023, 2024, 2025)]})
    add(store, "style_cash_flow", {"issuer_type": "ordinary", "currency": "CNY", "scope": "company_consolidated",
        "parent_profit_scope_reconciled": True, "annual": [
            {"year": year, "operating_cash_flow_yuan": "79", "disclosed_on": f"{year+1}-04-01"}
            for year in (2023, 2024, 2025)]}, reviewed=reviewed)
    add(store, "style_cash_review", {"issuer_type": "ordinary", "verdict": "clear", "basis": "合成现金专项核验"})


def test_cash_hard_failure_is_specific_to_dividend_value(tmp_path):
    store, _ = prepare(tmp_path)
    cash_facts(store)
    before = store.summary()
    report = stock_admission.evaluate_store(store, AS_OF, include_styles=True)
    item = report["items"][0]
    assert item["pools"]["dividend_value"]["approved_conditions_status"] == "failed"
    assert item["pools"]["growth_trend"]["approved_conditions_status"] != "failed"
    assert item["pools"]["dividend_value"]["admission_status"] == "excluded"
    assert item["pools"]["growth_trend"]["admission_status"] == "pending_verification"
    assert report["formal_screening_enabled"] is False
    assert report["members_published"] == 0
    assert store.summary() == before


def test_unreviewed_cash_flow_cannot_trigger_known_failure(tmp_path):
    store, _ = prepare(tmp_path)
    cash_facts(store, reviewed=False)
    item = stock_admission.evaluate_store(store, AS_OF, include_styles=True)["items"][0]
    assert item["pools"]["dividend_value"]["approved_conditions_status"] == "pending_verification"
    assert item["pools"]["dividend_value"]["admission_status"] == "pending_verification"


def test_nested_style_results_invalidated_on_revision_change(tmp_path, monkeypatch):
    from radar.stocks import style

    store, _ = prepare(tmp_path)
    cash_facts(store)
    original = style.evaluate_stock_styles

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        add(store, "research_hint", {"text": "计算中资料更新"}, reviewed=False)
        return result

    monkeypatch.setattr(style, "evaluate_stock_styles", changed)
    item = stock_admission.evaluate_store(store, AS_OF, include_styles=True)["items"][0]
    assert item["evaluation_valid"] is False
    assert item["common_status"] == "pending_verification"
    for pool in item["pools"].values():
        assert pool["admission_status"] == "pending_verification"
        assert pool["approved_conditions_status"] == "pending_verification"
        assert pool["opportunity_status"] == "unavailable"
        for branch in pool.get("branches", {}).values():
            assert branch["approved_conditions_status"] == "pending_verification"
        assert all(check["outcome"] == "pending_verification" for check in pool["checks"])


def test_style_error_has_two_explicit_unavailable_pools(tmp_path, monkeypatch):
    from radar.stocks import style

    store, _ = prepare(tmp_path)

    def failed(*args, **kwargs):
        raise ValueError("合成风格失败")

    monkeypatch.setattr(style, "evaluate_stock_styles", failed)
    item = stock_admission.evaluate_store(store, AS_OF, include_styles=True)["items"][0]
    assert item["evaluation_valid"] is False
    assert set(item["pools"]) == {"dividend_value", "growth_trend"}
    assert all(pool["admission_status"] == "pending_verification" for pool in item["pools"].values())
    assert item["style_rule_version"]


def test_style_command_export_and_database_protection(tmp_path):
    from stock_robot.cli import main

    store, db = prepare(tmp_path)
    cash_facts(store)
    output = tmp_path / "styles.json"
    args = ["radar", "stock-style-evaluate", "--db", str(db), "--as-of", AS_OF.isoformat(), "--output"]
    result = CliRunner().invoke(main, [*args, str(output)])
    assert result.exit_code == 0, result.output
    report: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
    assert report["items"][0]["pools"]["dividend_value"]["admission_status"] == "excluded"
    assert report["research_only"] is True
    assert report["style_rule_version"]
    rejected = CliRunner().invoke(main, [*args, str(db) + "-wal"])
    assert rejected.exit_code != 0
    assert StockResearchStore(db).summary()["companies"] == 1


def test_original_command_preserves_common_only_report(tmp_path):
    store, _ = prepare(tmp_path)
    cash_facts(store)
    result = stock_admission.evaluate_store(store, AS_OF)
    assert "pools" not in result["items"][0]
    assert "style_rule_version" not in result

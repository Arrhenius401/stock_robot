"""共同准入命令的真实SQLite审核、修订及文件边界。"""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from click.testing import CliRunner

from radar.stocks.research_store import StockResearchStore
from stock_robot import stock_admission
from stock_robot.stock_admission import admission_evaluate, evaluate_store

AS_OF = date(2026, 10, 15)


def prepare(tmp_path, symbols=("600900",)):
    store = StockResearchStore(tmp_path / "research.db", clock=lambda: datetime(2026, 10, 15, 1, tzinfo=UTC))
    store.import_companies([{"symbol": code, "name": "合成公司", "verified": True} for code in symbols],
                           source_sha256="a" * 64, source_label="合成候选", data_as_of=AS_OF)
    return store, tmp_path / "research.db"


def add_security(store, *, review=False):
    company = store.get_company("600900")
    assert company is not None
    added = store.add_evidence("600900", kind="admission_security",
                               payload={"exchange": "SSE", "security_type": "ordinary_a", "a_listed_on": "2025-01-01", "issuer_type": "ordinary"},
                               source_url="https://example.com/synthetic.pdf", disclosed_on=AS_OF,
                               content_sha256="b" * 64, expected_revision=company["revision"])
    if review:
        company = store.get_company("600900")
        assert company is not None
        store.save_review("600900", added["evidence_id"], expected_revision=company["revision"],
                          decision="confirmed", reviewer="合成测试", reason="验证上市年限硬条件")


def test_unreviewed_facts_do_not_pass_and_report_does_not_mutate(tmp_path):
    store, db = prepare(tmp_path)
    add_security(store)
    before = store.summary()
    result = CliRunner().invoke(admission_evaluate, ["--db", str(db), "--as-of", AS_OF.isoformat()])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["summary"] == {"total": 1, "common_passed": 0, "common_failed": 0, "pending_verification": 1, "eligible_members": 0}
    assert report["formal_screening_enabled"] is False
    assert report["members_published"] == 0
    assert store.summary() == before
    current = store.get_company("600900")
    assert current is not None
    assert current["admission_status"] == "pending_verification"


def test_reviewed_numbers_are_calculated_not_manual_admission(tmp_path):
    store, _ = prepare(tmp_path)
    add_security(store, review=True)
    report = evaluate_store(store, AS_OF)
    company = report["items"][0]
    checks = {row["code"]: row for row in company["checks"]}
    assert checks["listing_age"]["outcome"] == "failed"
    assert company["common_status"] == "failed"
    assert company["admission_status"] == "excluded"
    assert report["members_published"] == 0
    current = store.get_company("600900")
    assert current is not None
    assert current["admission_status"] == "pending_verification"


def test_revision_changed_during_evaluation_is_invalidated(tmp_path, monkeypatch):
    store, _ = prepare(tmp_path)
    original = stock_admission.evaluate_common_admission

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        add_security(store)
        return result

    monkeypatch.setattr(stock_admission, "evaluate_common_admission", changed)
    report = evaluate_store(store, AS_OF)
    assert report["items"][0]["common_status"] == "pending_verification"
    assert report["items"][0]["checks"][-1]["code"] == "source_revision"
    assert report["errors"] == [{"symbol": "600900", "reason": "source_revision_changed"}]


def test_one_company_failure_retains_other_results(tmp_path, monkeypatch, caplog):
    store, _ = prepare(tmp_path, ("600900", "000001"))
    original = stock_admission.evaluate_common_admission

    def isolated(symbol, *args, **kwargs):
        if symbol == "600900":
            raise ValueError("合成计算失败")
        return original(symbol, *args, **kwargs)

    monkeypatch.setattr(stock_admission, "evaluate_common_admission", isolated)
    report = evaluate_store(store, AS_OF)
    assert report["summary"]["total"] == 2
    assert report["summary"]["pending_verification"] == 2
    assert len(report["errors"]) == 1
    failed = next(row for row in report["items"] if row["symbol"] == "600900")
    assert failed["rule_version"] == report["rule_version"]
    assert failed["as_of"] == AS_OF.isoformat()
    assert failed["evidence_ids"] == []
    assert "合成计算失败" in caplog.text


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_output_cannot_overwrite_database_family(tmp_path, suffix):
    _, db = prepare(tmp_path)
    result = CliRunner().invoke(admission_evaluate, ["--db", str(db), "--as-of", AS_OF.isoformat(), "--output", str(db) + suffix])
    assert result.exit_code != 0
    assert StockResearchStore(db).summary()["companies"] == 1


@pytest.mark.parametrize("mutation", ["short", "duplicate", "future", "unordered", "non_string"])
def test_invalid_market_window_rejected(tmp_path, mutation):
    _, db = prepare(tmp_path)
    days: list[Any] = [(AS_OF - timedelta(days=59-i)).isoformat() for i in range(60)]
    if mutation == "short":
        days.pop()
    elif mutation == "duplicate":
        days[-1] = days[-2]
    elif mutation == "future":
        days[-1] = "2026-10-16"
    elif mutation == "unordered":
        days.reverse()
    else:
        days[-1] = 1
    window = tmp_path / "window.json"
    window.write_text(json.dumps(days), encoding="utf-8")
    result = CliRunner().invoke(admission_evaluate, ["--db", str(db), "--as-of", AS_OF.isoformat(), "--market-window", str(window)])
    assert result.exit_code != 0


def test_export_research_context_and_protect_window(tmp_path):
    _, db = prepare(tmp_path)
    days: list[Any] = [(AS_OF - timedelta(days=59-i)).isoformat() for i in range(60)]
    window = tmp_path / "window.json"
    raw = json.dumps(days)
    window.write_text(raw, encoding="utf-8")
    output = tmp_path / "report.json"
    args = ["--db", str(db), "--as-of", AS_OF.isoformat(), "--market-window", str(window), "--output"]
    result = CliRunner().invoke(admission_evaluate, [*args, str(output)])
    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["market_window_role"] == "research_context_only"
    assert report["market_window_source"] is not None
    rejected = CliRunner().invoke(admission_evaluate, [*args, str(window)])
    assert rejected.exit_code != 0
    assert window.read_text(encoding="utf-8") == raw


def test_command_registered_and_unknown_company_rejected(tmp_path):
    from stock_robot.cli import main

    result = CliRunner().invoke(main, ["radar", "stock-admission-evaluate", "--help"])
    assert result.exit_code == 0, result.output
    _, db = prepare(tmp_path)
    result = CliRunner().invoke(admission_evaluate, ["--db", str(db), "--as-of", AS_OF.isoformat(), "--symbol", "000001"])
    assert result.exit_code != 0


def test_initial_company_read_failure_is_isolated(tmp_path, monkeypatch):
    store, _ = prepare(tmp_path, ("600900", "000001"))
    original = store.get_company

    def isolated(symbol):
        if symbol == "600900":
            raise ValueError("合成读取失败")
        return original(symbol)

    monkeypatch.setattr(store, "get_company", isolated)
    report = evaluate_store(store, AS_OF)
    assert report["summary"]["total"] == 2
    assert report["errors"] == [{"symbol": "600900", "reason": "合成读取失败"}]
    assert next(row for row in report["items"] if row["symbol"] == "600900")["source_revision"] is None


def window_envelope():
    return {"version": "stock-market-window-v1", "market": "XSHG", "as_of": AS_OF.isoformat(),
            "days": [(AS_OF - timedelta(days=59-i)).isoformat() for i in range(60)],
            "research_only": True, "formal_screening_enabled": False}


def test_market_window_envelope_keeps_research_role_and_source_trace(tmp_path):
    _, db = prepare(tmp_path)
    path = tmp_path / "window.json"
    path.write_text(json.dumps(window_envelope()), encoding="utf-8")
    result = CliRunner().invoke(admission_evaluate, ["--db", str(db), "--as-of", AS_OF.isoformat(), "--market-window", str(path)])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["market_window_role"] == "research_context_only"
    assert report["formal_screening_enabled"] is False
    assert report["summary"]["common_passed"] == 0
    assert report["market_window_source"]["sha256"]


@pytest.mark.parametrize("field,value", [("version", "other"), ("market", "HKEX"), ("as_of", "2026-10-14"),
    ("research_only", False), ("formal_screening_enabled", True), ("days", None)])
def test_bad_market_window_envelope_rejected(tmp_path, field, value):
    payload = window_envelope()
    payload[field] = value
    path = tmp_path / "window.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        stock_admission._read_market_window(AS_OF, path)

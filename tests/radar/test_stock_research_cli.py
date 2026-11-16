"""共享研究CLI的真实SQLite往返与证据审核边界。"""

import json
from datetime import date

from click.testing import CliRunner

from radar.stocks.research_store import StockResearchStore
from stock_robot.stock_research import (
    add_evidence,
    import_records,
    list_records,
    review_evidence,
)


def invoke(command, args):
    result = CliRunner().invoke(command, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def prepare(tmp_path, rows=None):
    source = tmp_path / "companies.json"
    source.write_text(json.dumps({"companies": rows if rows is not None else [{"symbol": "600900", "name": "长江电力"}]}), encoding="utf-8")
    db = tmp_path / "radar.db"
    result = invoke(import_records, ["--kind", "candidates", "--input", str(source), "--db", str(db), "--as-of", "2026-10-14"])
    return source, db, result


def test_import_and_repeat_do_not_publish_or_duplicate(tmp_path):
    source, db, first = prepare(tmp_path)
    second = invoke(import_records, ["--kind", "candidates", "--input", str(source), "--db", str(db), "--as-of", "2026-10-14"])
    assert first["companies_added"] == 1
    assert second["reused"] is True
    assert first["members_published"] == second["members_published"] == 0
    data = invoke(list_records, ["--db", str(db)])
    assert data["total"] == 1
    assert data["page_size"] == 15
    assert data["items"][0]["admission_status"] == "pending_verification"


def test_detail_export_and_complete_synthetic_evidence_review(tmp_path):
    _, db, _ = prepare(tmp_path)
    store = StockResearchStore(db)
    company = store.get_company("600900")
    assert company is not None
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"symbol": "600900", "kind": "financial", "payload": {"metric": "parent_net_profit", "amount_yuan": "100"},
                                    "source_url": "https://example.com/synthetic.pdf", "disclosed_on": "2026-10-01", "content_sha256": "a" * 64}), encoding="utf-8")
    added = invoke(add_evidence, ["--input", str(evidence), "--db", str(db), "--expected-revision", str(company["revision"])])
    updated = store.get_company("600900")
    assert updated is not None
    reviewed = invoke(review_evidence, ["--db", str(db), "--symbol", "600900", "--evidence-id", added["evidence_id"],
                                        "--expected-revision", str(updated["revision"]), "--decision", "confirmed", "--reviewer", "合成测试", "--reason", "仅验证审核记录"])
    assert reviewed["members_published"] == 0
    output = tmp_path / "export.json"
    result = CliRunner().invoke(list_records, ["--db", str(db), "--symbol", "600900", "--output", str(output)])
    assert result.exit_code == 0, result.output
    exported = json.loads(output.read_text(encoding="utf-8"))
    assert exported["company"]["admission_status"] == "pending_verification"
    assert len(exported["reviews"]) == 1
    row = next(row for row in exported["evidence"] if row["evidence_id"] == added["evidence_id"])
    assert row["review_status"] == "confirmed"


def test_stale_revision_is_rejected_without_duplicate_review(tmp_path):
    _, db, _ = prepare(tmp_path)
    store = StockResearchStore(db)
    company = store.get_company("600900")
    assert company is not None
    revision = company["revision"]
    added = store.add_evidence("600900", kind="governance", payload={"text": "synthetic"}, source_url="https://example.com/test.pdf",
                               disclosed_on=date(2026, 10, 1), content_sha256="b" * 64, expected_revision=revision)
    result = CliRunner().invoke(review_evidence, ["--db", str(db), "--symbol", "600900", "--evidence-id", added["evidence_id"],
                                                  "--expected-revision", str(revision), "--decision", "confirmed", "--reviewer", "测试", "--reason", "测试"])
    assert result.exit_code != 0
    assert store.review_history("600900") == []


def test_database_and_wal_paths_cannot_be_overwritten(tmp_path):
    source = tmp_path / "input.db"
    raw = '{"companies":[]}'
    source.write_text(raw, encoding="utf-8")
    result = CliRunner().invoke(import_records, ["--kind", "candidates", "--input", str(source), "--db", str(source), "--as-of", "2026-10-14"])
    assert result.exit_code != 0
    assert source.read_text(encoding="utf-8") == raw
    _, db, _ = prepare(tmp_path)
    result = CliRunner().invoke(list_records, ["--db", str(db), "--output", str(db) + "-wal"])
    assert result.exit_code != 0


def test_self_asserted_verification_stays_unreviewed(tmp_path):
    _, db, _ = prepare(tmp_path, [{"symbol": "600900", "name": "test", "financial_verified": True, "status": "confirmed"}])
    data = invoke(list_records, ["--db", str(db), "--symbol", "600900"])
    assert data["company"]["admission_status"] == "pending_verification"
    assert data["reviews"] == []
    assert all(row["review_status"] == "pending_verification" for row in data["evidence"])


def test_commands_registered():
    from stock_robot.cli import main
    for name in ("stock-records-import", "stock-records-list", "stock-evidence-add", "stock-evidence-review"):
        result = CliRunner().invoke(main, ["radar", name, "--help"])
        assert result.exit_code == 0, result.output

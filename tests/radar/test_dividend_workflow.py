"""批量研究工作流的隔离、续跑和证据边界。"""
import hashlib
import json
from datetime import date

import pytest
from click.testing import CliRunner

from stock_robot import dividend_workflow as workflow


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    calls = {"extract": 0, "discover": 0, "archive": 0}
    source = tmp_path / "companies.json"
    write(source, {"companies": [{"symbol": "600900"}, {"symbol": "000333"}]})
    def discover(companies, cutoff, cache, **kwargs):
        calls["discover"] += 1
        return {"companies": [{"symbol": c["symbol"], "status": "query_scope_complete"} for c in companies], "documents": []}
    def archive(documents, root, **kwargs):
        calls["archive"] += 1
        root.mkdir(parents=True, exist_ok=True)
        file = root / "sample.pdf"
        if not file.exists():
            file.write_bytes(b"%PDF-original")
        return {"items": [{"symbol": "600900", "status": "cached", "file": file.name, "sha256": hashlib.sha256(b"%PDF-original").hexdigest()}]}
    def extract(archived, root, cutoff):
        calls["extract"] += 1
        return {"items": [{"symbol": "600900", "status": "matched_template", "event_proposals": [{"amount_yuan": "123"}]}], "event_proposals": []}
    monkeypatch.setattr(workflow, "discover_announcements", discover)
    monkeypatch.setattr(workflow, "collect_documents", archive)
    monkeypatch.setattr(workflow, "extract_archived_documents", extract)
    monkeypatch.setattr(workflow, "build_review_packet", lambda companies, extraction, prepared, research, cutoff: {"companies": companies})
    return source, tmp_path / "work", calls


def test_workflow_keeps_every_company_and_never_promotes_proposals(pipeline):
    source, root, calls = pipeline
    result = workflow.run_workflow(source, root, date(2026, 10, 14))
    prepared = json.loads((root / "prepared.json").read_text())
    assert len(prepared["companies"]) == 2
    assert all(c["events"] == [] for c in prepared["companies"])
    assert result["summary"]["research_pending"] == 2
    assert result["rule_enabled"] is False
    assert result["research_only"] is True
    workflow.run_workflow(source, root, date(2026, 10, 14))
    assert calls == {"extract": 1, "discover": 2, "archive": 2}


def test_cache_checks_pdf_bytes_and_result_checksum(pipeline):
    source, root, calls = pipeline
    workflow.run_workflow(source, root, date(2026, 10, 14))
    (root / "pdf-archive" / "sample.pdf").write_bytes(b"%PDF-changed")
    workflow.run_workflow(source, root, date(2026, 10, 14))
    assert calls["extract"] == 2
    write(root / "extracted.json", {"items": []})
    workflow.run_workflow(source, root, date(2026, 10, 14))
    assert calls["extract"] == 3


@pytest.mark.parametrize("change", ["input", "cutoff"])
def test_workspace_binding_rejects_changed_roster_or_cutoff(pipeline, change):
    source, root, _ = pipeline
    workflow.run_workflow(source, root, date(2026, 10, 14))
    if change == "input":
        write(source, [{"symbol": "601088"}])
    with pytest.raises(ValueError, match="绑定"):
        workflow.run_workflow(source, root, date(2026, 10, 15) if change == "cutoff" else date(2026, 10, 14))


def test_inputs_cannot_be_inside_workspace(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    source = root / "input.json"
    write(source, [])
    with pytest.raises(ValueError, match="外"):
        workflow.run_workflow(source, root, date(2026, 10, 14))


def test_evidence_updates_without_changing_workspace_binding(pipeline, tmp_path):
    source, root, _ = pipeline
    evidence = tmp_path / "evidence.json"
    write(evidence, {"companies": [{"symbol": "600900", "events": [{"id": "manual"}]}]})
    first = workflow.run_workflow(source, root, date(2026, 10, 14), evidence)
    write(evidence, {"companies": [{"symbol": "600900", "events": []}]})
    second = workflow.run_workflow(source, root, date(2026, 10, 14), evidence)
    assert first["evidence_source"]["sha256"] != second["evidence_source"]["sha256"]
    assert json.loads((root / "prepared.json").read_text())["companies"][0]["events"] == []


def test_failed_rerun_replaces_old_success_and_preserves_companies(pipeline, monkeypatch):
    source, root, _ = pipeline
    workflow.run_workflow(source, root, date(2026, 10, 14))
    def failed(*args, **kwargs):
        raise OSError("network unavailable")
    monkeypatch.setattr(workflow, "discover_announcements", failed)
    result = workflow.run_workflow(source, root, date(2026, 10, 14), refresh=True)
    assert result["status"] == "partial"
    assert result["errors"][0]["stage"] == "discovery"
    assert result["summary"]["query_scope_complete"] == 0
    assert len(json.loads((root / "review-packet.json").read_text())["companies"]) == 2
    assert json.loads((root / "summary.json").read_text())["status"] == "partial"


def test_invalid_evidence_preserves_previous_summary(pipeline, tmp_path):
    source, root, _ = pipeline
    workflow.run_workflow(source, root, date(2026, 10, 14))
    evidence = tmp_path / "bad.json"
    write(evidence, {"companies": [{"symbol": "outside"}]})
    with pytest.raises(ValueError):
        workflow.run_workflow(source, root, date(2026, 10, 14), evidence)
    assert json.loads((root / "summary.json").read_text())["status"] == "partial"


def test_cli_reports_pending_not_financial_success(pipeline):
    source, root, _ = pipeline
    result = CliRunner().invoke(workflow.dividend_workflow, ["--input", str(source), "--workspace", str(root), "--as-of", "2026-10-14"])
    assert result.exit_code == 0, result.output
    assert "待核验2" in result.output
    assert "未启用" in result.output



def test_refresh_and_parser_version_force_extraction(pipeline, monkeypatch):
    source, root, calls = pipeline
    workflow.run_workflow(source, root, date(2026, 10, 14))
    workflow.run_workflow(source, root, date(2026, 10, 14), refresh=True)
    assert calls["extract"] == 2
    monkeypatch.setattr(workflow, "WORKFLOW_VERSION", "new-version")
    with pytest.raises(ValueError, match="绑定"):
        workflow.run_workflow(source, root, date(2026, 10, 14))


def test_failed_archive_never_reuses_matched_extraction(pipeline, monkeypatch):
    source, root, calls = pipeline
    workflow.run_workflow(source, root, date(2026, 10, 14))
    def failed(documents, archive, **kwargs):
        return {"items": [{"symbol": "600900", "status": "failed", "file": "sample.pdf", "sha256": hashlib.sha256(b"%PDF-original").hexdigest()}]}
    monkeypatch.setattr(workflow, "collect_documents", failed)
    result = workflow.run_workflow(source, root, date(2026, 10, 14))
    assert calls["extract"] == 2
    assert result["summary"]["archive_failed"] == 1


def test_missing_pdf_never_reuses_extraction(pipeline, monkeypatch):
    source, root, calls = pipeline
    workflow.run_workflow(source, root, date(2026, 10, 14))
    archived = json.loads((root / "archived.json").read_text())
    monkeypatch.setattr(workflow, "collect_documents", lambda *args, **kwargs: archived)
    (root / "pdf-archive" / "sample.pdf").unlink()
    workflow.run_workflow(source, root, date(2026, 10, 14))
    assert calls["extract"] == 2


def test_pdf_size_mismatch_invalidates_cache(pipeline, monkeypatch):
    source, root, calls = pipeline
    workflow.run_workflow(source, root, date(2026, 10, 14))
    archived = json.loads((root / "archived.json").read_text())
    archived["items"][0]["bytes"] = 999
    monkeypatch.setattr(workflow, "collect_documents", lambda *args, **kwargs: archived)
    workflow.run_workflow(source, root, date(2026, 10, 14))
    assert calls["extract"] == 2


def test_unexpected_cache_failure_leaves_failed_checkpoint(pipeline, monkeypatch):
    source, root, _ = pipeline
    def failed(*args):
        raise OSError("parser source unreadable")
    monkeypatch.setattr(workflow, "_extraction_key", failed)
    with pytest.raises(OSError, match="parser source"):
        workflow.run_workflow(source, root, date(2026, 10, 14))
    summary = json.loads((root / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["errors"][0]["stage"] == "extraction_cache"


def test_prepared_write_failure_records_failed_checkpoint(pipeline, monkeypatch):
    source, root, _ = pipeline
    original = workflow._write_json
    def write_failed(path, value):
        if path.name == "prepared.json":
            raise OSError("preparation output unavailable")
        original(path, value)
    monkeypatch.setattr(workflow, "_write_json", write_failed)
    with pytest.raises(OSError):
        workflow.run_workflow(source, root, date(2026, 10, 14))
    summary = json.loads((root / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["errors"][0]["stage"] == "preparation"


@pytest.mark.parametrize("field", ["required_share_classes", "profits", "coverage", "events"])
def test_malformed_evidence_is_rejected_before_collection_and_preserves_summary(pipeline, tmp_path, field):
    source, root, calls = pipeline
    workflow.run_workflow(source, root, date(2026, 10, 14))
    previous = (root / "summary.json").read_bytes()
    previous_calls = dict(calls)
    evidence = tmp_path / "bad-format.json"
    write(evidence, {"companies": [{"symbol": "600900", field: "not-a-list"}]})
    with pytest.raises(TypeError, match="必须是列表"):
        workflow.run_workflow(source, root, date(2026, 10, 14), evidence)
    assert calls == previous_calls
    assert (root / "summary.json").read_bytes() == previous


def test_transient_replace_denial_retries_without_changing_permissions(tmp_path, monkeypatch):
    original = workflow._atomic_write
    calls = []
    delays = []
    def transient(path, value):
        calls.append(path)
        if len(calls) < 3:
            raise PermissionError("temporarily locked")
        original(path, value)
    monkeypatch.setattr(workflow, "_atomic_write", transient)
    monkeypatch.setattr(workflow.time, "sleep", delays.append)
    output = tmp_path / "state.json"
    workflow._write_json(output, {"status": "pending"})
    assert json.loads(output.read_text()) == {"status": "pending"}
    assert len(calls) == 3
    assert delays == [0.05, 0.1]


def test_persistent_replace_denial_still_raises_and_preserves_old_file(tmp_path, monkeypatch):
    output = tmp_path / "state.json"
    output.write_text("previous", encoding="utf-8")
    calls = []
    def denied(*args):
        calls.append(1)
        raise PermissionError("persistently denied")
    monkeypatch.setattr(workflow, "_atomic_write", denied)
    monkeypatch.setattr(workflow.time, "sleep", lambda *_args: None)
    with pytest.raises(PermissionError, match="persistently denied"):
        workflow._write_json(output, {"status": "new"})
    assert len(calls) == 3
    assert output.read_text() == "previous"


def test_direct_workflow_rejects_invalid_cutoff_before_creating_outputs(pipeline):
    source, root, calls = pipeline
    with pytest.raises(ValueError, match="三个完整年度"):
        workflow.run_workflow(source, root, date(1, 1, 1))
    assert not root.exists()
    assert calls == {"extract": 0, "discover": 0, "archive": 0}


def test_annual_mode_has_independent_stages_and_never_promotes_financials(pipeline, monkeypatch):
    source, root, _ = pipeline
    modes = []
    original = workflow.discover_announcements
    def discover(*args, **kwargs):
        modes.append(kwargs.get("mode", "dividend"))
        return original(*args, **kwargs)
    monkeypatch.setattr(workflow, "discover_announcements", discover)
    monkeypatch.setattr(workflow, "extract_archived_financials", lambda *_args: {"items": [], "financial_proposals": []})
    result = workflow.run_workflow(source, root, date(2026, 10, 14), annual_reports=True)
    assert modes == ["dividend", "annual_report"]
    assert result["summary"]["financial_companies_with_proposals"] == 0
    assert result["status"] == "partial"
    assert result["stages"]["annual_discovery"]["file"] == "annual-discovery.json"
    prepared = json.loads((root / "prepared.json").read_text())
    assert all(row["profits"] == [] for row in prepared["companies"])


def test_dividend_query_429_stops_annual_query_in_same_workflow(pipeline, monkeypatch):
    source, root, _ = pipeline
    calls = []
    def limited(*args, **kwargs):
        calls.append(kwargs.get("mode", "dividend"))
        return {"companies": [], "documents": [], "source_stopped": True}
    monkeypatch.setattr(workflow, "discover_announcements", limited)
    monkeypatch.setattr(workflow, "extract_archived_financials", lambda *_args: {"items": [], "financial_proposals": []})
    result = workflow.run_workflow(source, root, date(2026, 10, 14), annual_reports=True)
    assert calls == ["dividend"]
    assert result["stages"]["annual_discovery"]["status"] == "failed"
    assert any("429" in error["message"] for error in result["errors"])

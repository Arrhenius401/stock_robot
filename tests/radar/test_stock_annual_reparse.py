"""离线重解析保持源归档不可变，损坏公司独立失败。"""
import json
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from radar.stocks import annual_reparse
from radar.stocks.annual_batch import VERSION, _bytes, _save, _sha
from radar.stocks.annual_reparse import run_annual_reparse

AS_OF = date(2026, 10, 17)
COMPANIES = [{"symbol": "000333", "name": "美的集团"}, {"symbol": "600900", "name": "长江电力"}]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        pytest.fail("离线重解析禁止网络请求")
    monkeypatch.setattr("requests.sessions.Session.request", fail)
    monkeypatch.setattr("urllib.request.urlopen", fail)


def make_source(root: Path, *, committed: int = 2) -> Path:
    root.mkdir()
    _save(root / "manifest.json", {"version": VERSION, "as_of": AS_OF.isoformat(),
                                  "companies": COMPANIES, "input_sha256": _sha(_bytes(COMPANIES))})
    for index, company in enumerate(COMPANIES):
        directory = root / "companies" / company["symbol"]
        archive = directory / "archive"
        archive.mkdir(parents=True)
        writer, buffer = PdfWriter(), BytesIO()
        writer.add_blank_page(width=72, height=72)
        writer.write(buffer)
        payload = buffer.getvalue()
        documents, items = [], []
        for year in (2023, 2024, 2025):
            url = f"https://static.cninfo.com.cn/{company['symbol']}-{year}.pdf"
            name = _sha(url.encode()) + ".pdf"
            (archive / name).write_bytes(payload)
            document = {"symbol": company["symbol"], "fiscal_year": year,
                        "discovery_status": "discovered", "source_url": url}
            documents.append(document)
            items.append({**document, "file": name, "status": "downloaded", "bytes": len(payload), "sha256": _sha(payload)})
        result = {**company, "status": "partial", "source_stopped": False, "unresolved": [], "excluded": [],
                  "discovery": {"companies": [{**company, "status": "query_scope_complete"}], "documents": documents},
                  "archive": {"items": items}, "extraction": {"items": []}}
        digest = _save(directory / "result.json", result)
        if index < committed:
            _save(directory / "checkpoint.json", {"symbol": company["symbol"], "sha256": digest})
    return root


class Extractor:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, archive: dict[str, Any], directory: Path, as_of: date) -> dict[str, Any]:
        self.calls += 1
        assert as_of == AS_OF and directory.name == "archive"
        items = [{"symbol": item["symbol"], "source_url": item["source_url"], "disclosed_on": "2026-04-01",
                  "file_sha256": item["sha256"], "text_sha256": "b" * 64, "status": "matched_template",
                  "reasons": [], "currency_evidence": {"excerpt": "人民币元"},
                  "financial_proposals": [{"symbol": item["symbol"], "fiscal_year": year, "metric": "revenue",
                                           "amount_yuan": "100", "source_url": item["source_url"],
                                           "disclosed_on": "2026-04-01", "currency_evidence": {"excerpt": "人民币元"}}
                                          for year in (2022, 2023, 2024, 2025)]}
                 for item in archive["items"] if item["status"] in {"downloaded", "cached"}]
        return {"items": items, "financial_proposals": [p for item in items for p in item["financial_proposals"]]}


def change_result(source: Path, mutate: Any) -> None:
    directory = source / "companies" / "000333"
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    mutate(result)
    digest = _save(directory / "result.json", result)
    _save(directory / "checkpoint.json", {"symbol": "000333", "sha256": digest})


def test_reparse_resume_three_years_and_readonly(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    extract, packets = Extractor(), []
    target = tmp_path / "target"
    summary = run_annual_reparse(source, target, AS_OF, extract=extract, on_packet=packets.append)
    assert summary["completed"] == 2 and summary["financial_proposals"] > 0
    assert summary["companies_with_proposals"] == 2 and summary["reports_matched"] == 6
    assert summary["reports_pending"] == 0 and summary["members"] == 0
    assert summary["financial_verified"] is summary["formal_screening_enabled"] is False
    result = json.loads((target / "companies/000333/result.json").read_text(encoding="utf-8"))
    assert {p["fiscal_year"] for p in result["extraction"]["financial_proposals"]} == {2023, 2024, 2025}
    assert result["extraction"]["items"][0]["currency_evidence"]["excerpt"] == "人民币元"
    assert not list(target.rglob("*.pdf"))
    again = run_annual_reparse(source, target, AS_OF, extract=extract, on_packet=packets.append)
    assert again["reused"] == 2 and extract.calls == 2 and len(packets) == 4
    assert before == {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}


def test_active_source_and_incremental_checkpoint(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source", committed=1)
    target, extract = tmp_path / "target", Extractor()
    first = run_annual_reparse(source, target, AS_OF, extract=extract)
    assert first["pending"] == 1 and first["processed"] == 1
    directory = source / "companies/600900"
    _save(directory / "checkpoint.json", {"symbol": "600900", "sha256": _sha((directory / "result.json").read_bytes())})
    second = run_annual_reparse(source, target, AS_OF, extract=extract)
    assert second["completed"] == 2 and second["reused"] == 1 and extract.calls == 2


@pytest.mark.parametrize("broken", ["source_digest", "source_name", "cross_company", "pdf_missing", "pdf_changed", "target_digest", "target_source"])
def test_corruption_isolated_before_extract_or_callback(tmp_path: Path, broken: str) -> None:
    source = make_source(tmp_path / "source")
    target, extract, packets = tmp_path / "target", Extractor(), []
    if broken.startswith("target"):
        run_annual_reparse(source, target, AS_OF, extract=extract)
        directory = target / "companies/000333"
        if broken == "target_digest":
            (directory / "result.json").write_text("{}", encoding="utf-8")
        else:
            change_result(source, lambda result: result.update(excluded=[{"reason": "源已变更"}]))
    elif broken == "source_digest":
        (source / "companies/000333/result.json").write_text("{}", encoding="utf-8")
    elif broken == "source_name":
        change_result(source, lambda result: result.update(name="其他公司"))
    elif broken == "cross_company":
        change_result(source, lambda result: result["archive"]["items"][0].update(symbol="600900"))
    else:
        pdf = next((source / "companies/000333/archive").glob("*.pdf"))
        if broken == "pdf_missing":
            pdf.unlink()
        else:
            pdf.write_bytes(b"%PDF-changed")
    before = extract.calls
    summary = run_annual_reparse(source, target, AS_OF, extract=extract, on_packet=packets.append)
    assert summary["failed"] == 1 and summary["completed"] == 1 and len(packets) == 1
    assert extract.calls - before == (0 if broken.startswith("target") else 1)


@pytest.mark.parametrize("reason", ["query", "unresolved", "year", "archive"])
def test_source_incompleteness_survives_reparse(tmp_path: Path, reason: str) -> None:
    source = make_source(tmp_path / "source")
    def mutate(result: dict[str, Any]) -> None:
        if reason == "query":
            result["discovery"]["companies"][0]["status"] = "partial"
        elif reason == "unresolved":
            result["unresolved"].append({"reason": "歧义年报"})
        elif reason == "year":
            result["discovery"]["documents"].pop()
        else:
            result["archive"]["items"][0]["status"] = "failed"
    change_result(source, mutate)
    summary = run_annual_reparse(source, tmp_path / "target", AS_OF, extract=Extractor())
    assert summary["partial"] == 1 and summary["completed"] == 1


def test_callback_failure_checkpoint_and_retry(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    target, extract = tmp_path / "target", Extractor()
    def fail(packet: Any) -> None:
        raise OSError("数据库忙")
    with pytest.raises(RuntimeError, match="断点已保存"):
        run_annual_reparse(source, target, AS_OF, extract=extract, on_packet=fail)
    summary = json.loads((target / "summary.json").read_text(encoding="utf-8"))
    assert summary["import_failed"] == 1 and summary["failed"] == 0
    packets = []
    second = run_annual_reparse(source, target, AS_OF, extract=extract, on_packet=packets.append)
    assert second["reused"] == 1 and len(packets) == 2 and extract.calls == 2


def test_paths_date_manifest_and_parser_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_source(tmp_path / "source")
    for target in (source, source / "inside", tmp_path):
        with pytest.raises(ValueError, match="嵌套"):
            run_annual_reparse(source, target, AS_OF)
    with pytest.raises(ValueError, match="清单"):
        run_annual_reparse(source, tmp_path / "wrong-date", date(2025, 1, 1))
    target = tmp_path / "target"
    run_annual_reparse(source, target, AS_OF, extract=Extractor())
    parser = tmp_path / "parser.py"
    parser.write_text("# 新解析器", encoding="utf-8")
    monkeypatch.setattr(annual_reparse.financial_extract, "__file__", str(parser))
    with pytest.raises(ValueError, match="清单"):
        run_annual_reparse(source, target, AS_OF)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    manifest["input_sha256"] = "0" * 64
    _save(source / "manifest.json", manifest)
    with pytest.raises(ValueError, match="清单"):
        run_annual_reparse(source, tmp_path / "invalid", AS_OF)


def test_non_object_manifest_rejected(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    _save(source / "manifest.json", [])
    with pytest.raises(TypeError, match="清单必须为对象"):
        run_annual_reparse(source, tmp_path / "target", AS_OF)
    assert not (tmp_path / "target").exists()


def test_excluded_older_review_document_does_not_block(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    def mutate(result: dict[str, Any]) -> None:
        old = {"symbol": "000333", "fiscal_year": 2022, "discovery_status": "pending_review"}
        result["discovery"]["documents"].append(old)
        result["excluded"].append({"reason": "不在此前三个完整财政年度", "document": old})
    change_result(source, mutate)
    summary = run_annual_reparse(source, tmp_path / "target", AS_OF, extract=Extractor())
    assert summary["completed"] == 2


def test_default_extractor_blank_pdf_stays_pending(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    summary = run_annual_reparse(source, tmp_path / "target", AS_OF)
    assert summary["partial"] == 2 and summary["reports_pending"] == 6
    assert summary["financial_proposals"] == 0


def test_symlink_rejected_before_target_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_source(tmp_path / "source")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == source or original(path))
    with pytest.raises(ValueError, match="符号链接"):
        run_annual_reparse(source, tmp_path / "target", AS_OF)
    assert not (tmp_path / "target").exists()

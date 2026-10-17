"""年度资料批处理只验证采集完整性，不自动审核财务事实。"""

import hashlib
import json
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from radar.stocks.annual_batch import run_annual_batch
from stock_robot.dividend_materials import DEFAULT_HOSTS

AS_OF = date(2026, 10, 17)
COMPANIES = [{"symbol": "600900", "name": "长江电力"}, {"symbol": "000333", "name": "美的集团"}]


class Sources:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.extra: list[dict[str, Any]] = []
        self.stop_discovery = False
        self.stop_pdf = False
        self.cross_company = False
        self.fail = False

    def discover(self, companies: list[dict[str, Any]], as_of: date, cache: Path, *, mode: str) -> dict[str, Any]:
        assert as_of == AS_OF and mode == "annual_report"
        symbol = companies[0]["symbol"]
        self.calls.append(symbol)
        if self.fail and symbol == "000333":
            raise OSError("单公司网络失败")
        documents = [{"symbol": symbol, "fiscal_year": year, "discovery_status": "discovered",
                      "source_url": f"https://static.cninfo.com.cn/{symbol}-{year}.pdf"} for year in (2023, 2024, 2025)]
        documents.extend({**row, "symbol": symbol} for row in self.extra)
        if self.cross_company:
            documents[0]["symbol"] = "600901"
        return {"companies": [{"symbol": symbol, "status": "query_scope_complete"}],
                "documents": documents, "source_stopped": self.stop_discovery}

    def archive(self, documents: list[dict[str, Any]], directory: Path, *, allowed_hosts: set[str]) -> dict[str, Any]:
        assert allowed_hosts == set(DEFAULT_HOSTS)
        directory.mkdir(parents=True, exist_ok=True)
        writer, buffer = PdfWriter(), BytesIO()
        writer.add_blank_page(width=72, height=72)
        writer.write(buffer)
        payload = buffer.getvalue()
        items = []
        for document in documents:
            name = hashlib.sha256(document["source_url"].encode("utf-8")).hexdigest() + ".pdf"
            if not self.stop_pdf:
                (directory / name).write_bytes(payload)
            items.append({**document, "status": "failed" if self.stop_pdf else "downloaded",
                          "error": "HTTP 429" if self.stop_pdf else None,
                          "file": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
        return {"items": items}

    def extract(self, archive: dict[str, Any], directory: Path, as_of: date) -> dict[str, Any]:
        items = [{"symbol": row["symbol"], "source_url": row["source_url"], "disclosed_on": "2026-04-01",
                  "file_sha256": "a" * 64, "text_sha256": "b" * 64,
                  "status": "matched_template", "reasons": [],
                  "financial_proposals": [{"symbol": row["symbol"], "fiscal_year": year,
                                           "source_url": row["source_url"], "disclosed_on": "2026-04-01",
                                           "metric": "revenue", "amount_yuan": "100"}
                                          for year in (2022, 2023, 2024, 2025)]} for row in archive["items"]]
        return {"items": items, "financial_proposals": [proposal for row in items for proposal in row["financial_proposals"]]}

    def run(self, root: Path, companies: list[dict[str, Any]] | None = None, **kwargs: Any) -> dict[str, Any]:
        return run_annual_batch(COMPANIES if companies is None else companies, root, AS_OF,
                                discover=self.discover, archive=self.archive, extract=self.extract, **kwargs)


def test_three_year_pipeline_and_resume_callback(tmp_path: Path) -> None:
    sources = Sources()
    packets = []
    summary = sources.run(tmp_path / "batch", on_packet=packets.append)
    assert summary["status"] == "completed"
    assert summary["processed"] == summary["completed"] == 2
    assert summary["years"] == [2023, 2024, 2025]
    assert summary["formal_screening_enabled"] is False and summary["members"] == 0
    assert packets[0]["financial_verified"] is False
    assert all(proposal["fields"]["fiscal_year"] in summary["years"]
               for packet in packets for company in packet["companies"] for proposal in company["financial_proposals"])
    assert all(proposal["requires_review"] for packet in packets for company in packet["companies"]
               for proposal in company["financial_proposals"])
    second = sources.run(tmp_path / "batch", on_packet=packets.append)
    assert sources.calls == ["000333", "600900"]
    assert second["reused"] == 2 and len(packets) == 4
    result = tmp_path / "batch" / summary["companies"][0]["result_file"]
    assert hashlib.sha256(result.read_bytes()).hexdigest() == summary["companies"][0]["sha256"]


def test_unknown_and_review_documents_remain_unresolved(tmp_path: Path) -> None:
    sources = Sources()
    sources.extra = [{"fiscal_year": None, "source_url": "https://static.cninfo.com.cn/unknown.pdf"},
                     {"fiscal_year": 2024, "source_url": "https://static.cninfo.com.cn/correction.pdf",
                      "discovery_status": "pending_review"},
                     {"fiscal_year": 2022, "source_url": "https://static.cninfo.com.cn/old.pdf"}]
    summary = sources.run(tmp_path / "batch", COMPANIES[:1])
    result = json.loads((tmp_path / "batch" / summary["companies"][0]["result_file"]).read_text(encoding="utf-8"))
    assert summary["partial"] == 1
    assert len(result["unresolved"]) == 2 and len(result["excluded"]) == 1
    assert len(result["archive"]["items"]) == 4


@pytest.mark.parametrize("stop", ["stop_discovery", "stop_pdf"])
def test_rate_limit_stops_remaining_companies(tmp_path: Path, stop: str) -> None:
    sources = Sources()
    setattr(sources, stop, True)
    summary = sources.run(tmp_path / "batch")
    assert sources.calls == ["000333"]
    assert summary["source_stopped"] and summary["pending"] == 1
    assert "未发起采集" in summary["companies"][1]["reason"]
    setattr(sources, stop, False)
    resumed = sources.run(tmp_path / "batch")
    assert resumed["pending"] == 0 and sources.calls == ["000333", "600900"]


def test_import_failure_preserves_collection_checkpoint(tmp_path: Path) -> None:
    sources = Sources()

    def fail_import(packet: dict[str, Any]) -> None:
        raise OSError("数据库暂不可用")

    with pytest.raises(RuntimeError, match="采集断点已保存"):
        sources.run(tmp_path / "batch", on_packet=fail_import)
    summary = json.loads((tmp_path / "batch" / "summary.json").read_text(encoding="utf-8"))
    assert summary["failed"] == 0 and summary["import_failed"] == 1
    assert (tmp_path / "batch" / "companies" / "000333" / "checkpoint.json").is_file()
    packets = []
    summary = sources.run(tmp_path / "batch", on_packet=packets.append)
    assert summary["reused"] == 1 and len(packets) == 2
    assert sources.calls == ["000333", "600900"]


def test_corrupt_checkpoint_never_refetches(tmp_path: Path) -> None:
    sources = Sources()
    sources.run(tmp_path / "batch")
    path = tmp_path / "batch" / "companies" / "000333" / "result.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="摘要不一致"):
        sources.run(tmp_path / "batch")
    assert sources.calls == ["000333", "600900"]


@pytest.mark.parametrize("companies", [[], [{"symbol": "../x", "name": "非法"}],
    [{"symbol": "600900", "name": " "}], [{"symbol": "600900", "name": "长江电力 "}],
    [{"symbol": "600900", "name": "长江电力"}, {"symbol": "600900", "name": "冲突名称"}]])
def test_invalid_input_rejected_before_writes(tmp_path: Path, companies: list[dict[str, Any]]) -> None:
    with pytest.raises(ValueError):
        Sources().run(tmp_path / "batch", companies)
    assert not (tmp_path / "batch").exists()


def test_same_symbol_deduplicated_and_binding_rejects_changes(tmp_path: Path) -> None:
    sources = Sources()
    summary = sources.run(tmp_path / "batch", [COMPANIES[0], COMPANIES[0]])
    assert summary["processed"] == 1
    with pytest.raises(ValueError, match="清单"):
        sources.run(tmp_path / "batch")
    with pytest.raises(ValueError, match="清单"):
        run_annual_batch([COMPANIES[0]], tmp_path / "batch", date(2025, 1, 1))


def test_unbound_nonempty_workspace_rejected(tmp_path: Path) -> None:
    (tmp_path / "foreign.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="非空"):
        Sources().run(tmp_path)


def test_cross_company_rejected_and_network_failure_isolated(tmp_path: Path) -> None:
    sources = Sources()
    sources.cross_company = True
    summary = sources.run(tmp_path / "cross")
    assert summary["failed"] == 2
    assert all("跨公司" in row["error"] for row in summary["companies"])
    assert not list((tmp_path / "cross").rglob("result.json"))
    sources.cross_company, sources.fail = False, True
    summary = sources.run(tmp_path / "network")
    assert summary["failed"] == 1 and summary["completed"] == 1


@pytest.mark.parametrize("broken", ["missing_result", "bad_json", "wrong_symbol", "orphan_result"])
def test_broken_checkpoint_rejected_without_network(tmp_path: Path, broken: str) -> None:
    sources = Sources()
    sources.run(tmp_path / "batch")
    company = tmp_path / "batch" / "companies" / "000333"
    checkpoint, result = company / "checkpoint.json", company / "result.json"
    if broken == "missing_result":
        result.unlink()
    elif broken == "bad_json":
        checkpoint.write_text("{", encoding="utf-8")
    elif broken == "wrong_symbol":
        checkpoint.write_text(json.dumps({"symbol": "600900", "sha256": hashlib.sha256(result.read_bytes()).hexdigest()}), encoding="utf-8")
    else:
        checkpoint.unlink()
    with pytest.raises((ValueError, OSError)):
        sources.run(tmp_path / "batch")
    assert sources.calls == ["000333", "600900"]


@pytest.mark.parametrize("link_target", ["root", "ancestor", "child"])
def test_symlink_workspace_refused_without_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link_target: str) -> None:
    root = tmp_path / "batch"
    target = root if link_target == "root" else tmp_path
    if link_target == "child":
        root.mkdir()
        target = root / "linked.json"
        target.write_text("{}", encoding="utf-8")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == target or original(path))
    sources = Sources()
    with pytest.raises(ValueError, match="符号链接"):
        sources.run(root)
    assert sources.calls == [] and not (root / "manifest.json").exists()


def test_symbol_containing_429_does_not_stop_source(tmp_path: Path) -> None:
    summary = Sources().run(tmp_path / "batch", [{"symbol": "600429", "name": "三元股份"}])
    assert summary["status"] == "completed" and summary["source_stopped"] is False


@pytest.mark.parametrize("damage", ["delete", "rewrite", "truncate"])
def test_reuse_rejects_damaged_pdf_without_network_or_callback(tmp_path: Path, damage: str) -> None:
    sources = Sources()
    root = tmp_path / "batch"
    summary = sources.run(root)
    result_path = root / summary["companies"][0]["result_file"]
    original_result = result_path.read_bytes()
    record = json.loads(original_result)["archive"]["items"][0]
    pdf = result_path.parent / "archive" / record["file"]
    if damage == "delete":
        pdf.unlink()
    elif damage == "rewrite":
        payload = pdf.read_bytes()
        pdf.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    else:
        pdf.write_bytes(pdf.read_bytes()[:-1])
    packets = []
    with pytest.raises(ValueError, match="归档PDF"):
        sources.run(root, on_packet=packets.append)
    assert not packets and sources.calls == ["000333", "600900"]
    assert result_path.read_bytes() == original_result


@pytest.mark.parametrize("damage", ["missing", "bad_filename", "bad_length", "bad_sha"])
def test_initial_collection_rejects_unverifiable_archive(tmp_path: Path, damage: str) -> None:
    sources = Sources()
    root = tmp_path / "batch"

    def bad_archive(documents: list[dict[str, Any]], directory: Path, *, allowed_hosts: set[str]) -> dict[str, Any]:
        result = sources.archive(documents, directory, allowed_hosts=allowed_hosts)
        record = result["items"][0]
        if damage == "missing":
            (directory / record["file"]).unlink()
        elif damage == "bad_filename":
            record["file"] = "../outside.pdf"
        elif damage == "bad_length":
            record["bytes"] += 1
        else:
            record["sha256"] = "0" * 64
        return result

    packets = []
    summary = run_annual_batch(COMPANIES[:1], root, AS_OF, discover=sources.discover,
                               archive=bad_archive, extract=sources.extract, on_packet=packets.append)
    assert summary["failed"] == 1 and not packets
    assert "归档PDF" in summary["companies"][0]["error"]
    assert not list(root.rglob("checkpoint.json")) and not list(root.rglob("result.json"))

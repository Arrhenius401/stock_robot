"""年度批处理命令的导入边界及待审语义。"""

import json
from datetime import date

import pytest
from click.testing import CliRunner

from radar.stocks.research_store import StockResearchStore
from stock_robot import stock_annual_collect as module


def test_command_registers():
    from stock_robot.cli import main

    result = CliRunner().invoke(main, ["radar", "stock-annual-collect", "--help"])
    assert result.exit_code == 0, result.output


def test_import_failure_is_retryable_cli_error(tmp_path, monkeypatch):
    source = tmp_path / "input.json"
    source.write_text('[{"symbol":"600900","name":"合成公司"}]', encoding="utf-8")

    def fail(*args, **kwargs):
        raise RuntimeError("采集断点已保存，审核包导入失败，可重试")

    monkeypatch.setattr(module, "run_annual_batch", fail)
    result = CliRunner().invoke(
        module.annual_collect,
        ["--input", str(source), "--workspace", str(tmp_path / "work"), "--as-of", "2026-10-17"],
    )
    assert result.exit_code == 1
    assert "Error:" in result.output and "可重试" in result.output


def test_collect_can_import_pending_packet_without_review(tmp_path, monkeypatch):
    db = tmp_path / "research.db"
    store = StockResearchStore(db)
    store.import_companies(
        [{"symbol": "600900", "name": "合成公司"}],
        source_sha256="a" * 64,
        source_label="测试范围",
        data_as_of=date(2026, 10, 16),
    )
    source = tmp_path / "input.json"
    source.write_text(
        json.dumps({"companies": [{"symbol": "600900", "name": "合成公司"}]})
    )
    seen = []

    def batch(companies, workspace, cutoff, *, on_packet):
        assert cutoff == date(2026, 10, 17)
        packet = {
            "companies": [
                {
                    "symbol": "600900",
                    "name": "合成公司",
                    "financial_proposals": [],
                    "financial_documents": [],
                }
            ]
        }
        seen.append(on_packet(packet))
        seen.append(on_packet(packet))
        return {"status": "partial", "members_published": 0}

    monkeypatch.setattr(module, "run_annual_batch", batch)
    result = CliRunner().invoke(
        module.annual_collect,
        [
            "--input",
            str(source),
            "--workspace",
            str(tmp_path / "work"),
            "--as-of",
            "2026-10-17",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen[1]["reused"] is True
    assert store.summary()["reviews"] == 0
    assert all(
        item["review_status"] != "confirmed" for item in store.list_evidence("600900")
    )


@pytest.mark.parametrize("bad", ["inside", "database", "future"])
def test_invalid_paths_or_future_rejected_before_collection(tmp_path, monkeypatch, bad):
    db = tmp_path / "research.db"
    StockResearchStore(db)
    work = tmp_path if bad == "database" else tmp_path / "work"
    source = work / "input.json" if bad == "inside" else tmp_path / "input.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('{"companies":[]}')
    monkeypatch.setattr(
        module, "run_annual_batch", lambda *a, **k: pytest.fail("拒绝前不能采集")
    )
    result = CliRunner().invoke(
        module.annual_collect,
        [
            "--input",
            str(source),
            "--workspace",
            str(work),
            "--as-of",
            "9999-12-31" if bad == "future" else "2026-10-17",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code != 0

"""离线年报命令复用待审导入，不产生审核或成员。"""

from datetime import date

import pytest
from click.testing import CliRunner

from radar.stocks.research_store import StockResearchStore
from stock_robot import stock_annual_reparse as module


def test_reparse_command_registers():
    from stock_robot.cli import main

    result = CliRunner().invoke(main, ["radar", "stock-annual-reparse", "--help"])
    assert result.exit_code == 0, result.output


def test_reparse_can_replay_pending_import(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    db = tmp_path / "research.db"
    store = StockResearchStore(db)
    store.import_companies(
        [{"symbol": "600900", "name": "合成公司"}],
        source_sha256="a" * 64,
        source_label="离线测试",
        data_as_of=date(2026, 10, 17),
    )
    seen = []

    def reparse(source_workspace, workspace, cutoff, *, on_packet):
        assert source_workspace == source and cutoff == date(2026, 10, 17)
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
        return {"status": "partial", "members": 0}

    monkeypatch.setattr(module, "run_annual_reparse", reparse)
    result = CliRunner().invoke(
        module.annual_reparse,
        [
            "--source-workspace",
            str(source),
            "--workspace",
            str(tmp_path / "output"),
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
        item["review_status"] == "pending_verification"
        for item in store.list_evidence("600900")
    )


@pytest.mark.parametrize("bad", ["future", "same", "nested", "db_source", "db_output"])
def test_invalid_reparse_paths_or_date_rejected_before_extraction(
    tmp_path, monkeypatch, bad
):
    source = tmp_path / "source"
    source.mkdir()
    output = (
        source
        if bad == "same"
        else source / "new"
        if bad == "nested"
        else tmp_path / "output"
    )
    db = source / "research.db" if bad == "db_source" else tmp_path / "research.db"
    StockResearchStore(db)
    if bad == "db_output":
        output = tmp_path
    monkeypatch.setattr(
        module, "run_annual_reparse", lambda *a, **k: pytest.fail("拒绝前不能提取")
    )
    result = CliRunner().invoke(
        module.annual_reparse,
        [
            "--source-workspace",
            str(source),
            "--workspace",
            str(output),
            "--as-of",
            "9999-12-31" if bad == "future" else "2026-10-17",
            "--db",
            str(db),
        ],
    )
    assert result.exit_code != 0


def test_reparse_import_failure_is_retryable_cli_error(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()

    def fail(*args, **kwargs):
        raise RuntimeError("重解析断点已保存，导入失败，可重试")

    monkeypatch.setattr(module, "run_annual_reparse", fail)
    result = CliRunner().invoke(
        module.annual_reparse,
        [
            "--source-workspace",
            str(source),
            "--workspace",
            str(tmp_path / "output"),
            "--as-of",
            "2026-10-17",
        ],
    )
    assert result.exit_code == 1
    assert "Error:" in result.output and "可重试" in result.output

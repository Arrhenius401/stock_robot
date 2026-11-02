"""年报字段命令的核验边界及文件保护。"""
import json

from click.testing import CliRunner

from stock_robot.financial_documents import financial_extract


def test_financial_cli_preserves_review_flags(tmp_path, monkeypatch):
    import stock_robot.financial_documents as module
    source = tmp_path / "archive.json"
    source.write_text('{"items":[]}', encoding="utf-8")
    archive = tmp_path / "pdf"
    archive.mkdir()
    output = tmp_path / "out.json"
    monkeypatch.setattr(module, "extract_archived_financials", lambda *_args: {"financial_proposals": []})
    result = CliRunner().invoke(financial_extract, ["--archive-result", str(source), "--archive-dir", str(archive),
                                                   "--output", str(output), "--as-of", "2026-10-14"])
    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text())
    assert payload["requires_review"] is True
    assert payload["rule_enabled"] is payload["financial_verified"] is False


def test_financial_cli_protects_raw_archive(tmp_path):
    source = tmp_path / "archive.json"
    source.write_text('{"items":[]}', encoding="utf-8")
    archive = tmp_path / "pdf"
    archive.mkdir()
    result = CliRunner().invoke(financial_extract, ["--archive-result", str(source), "--archive-dir", str(archive),
                                                   "--output", str(archive / "out.json"), "--as-of", "2026-10-14"])
    assert result.exit_code != 0
    assert not (archive / "out.json").exists()


def test_financial_command_is_registered():
    from stock_robot.cli import main
    result = CliRunner().invoke(main, ["radar", "financial-extract", "--help"])
    assert result.exit_code != 0, result.output

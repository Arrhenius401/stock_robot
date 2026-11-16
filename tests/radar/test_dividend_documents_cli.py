"""发现与提取命令保持查询、建议与正式财务证据的边界。"""

import json

from click.testing import CliRunner

from stock_robot.dividend_documents import discover_dividends, extract_dividends


def test_discovery_cli_preserves_failure_scope_and_disables_rules(tmp_path, monkeypatch):
    import stock_robot.dividend_documents as module

    source = tmp_path / "companies.json"
    output = tmp_path / "found.json"
    source.write_text('{"companies":[{"symbol":"600900"}]}', encoding="utf-8")
    monkeypatch.setattr(module, "discover_announcements", lambda *_args, **_kwargs: {"documents": [], "query_states": [{"status": "failed"}]})
    result = CliRunner().invoke(discover_dividends, ["--input", str(source), "--output", str(output), "--as-of", "2026-10-14", "--cache-dir", str(tmp_path / "cache")])
    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["query_states"][0]["status"] == "failed"
    assert payload["financial_verified"] is False
    assert payload["rule_enabled"] is False
    assert "失败与不完整范围" in result.output


def test_discovery_does_not_overwrite_input(tmp_path):
    source = tmp_path / "companies.json"
    source.write_text('{"companies":[]}', encoding="utf-8")
    result = CliRunner().invoke(discover_dividends, ["--input", str(source), "--output", str(source), "--as-of", "2026-10-14", "--cache-dir", str(tmp_path / "cache")])
    assert result.exit_code != 0
    assert "覆盖" in result.output


def test_extract_cli_keeps_proposals_unverified(tmp_path, monkeypatch):
    import stock_robot.dividend_documents as module

    archive = tmp_path / "archive"
    archive.mkdir()
    source = tmp_path / "archive.json"
    output = tmp_path / "proposals.json"
    source.write_text('{"items":[]}', encoding="utf-8")
    monkeypatch.setattr(module, "extract_archived_documents", lambda *_args, **_kwargs: {"event_proposals": [{"requires_review": True}]})
    result = CliRunner().invoke(extract_dividends, ["--archive-result", str(source), "--archive-dir", str(archive), "--output", str(output), "--as-of", "2026-10-14"])
    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["event_proposals"][0]["requires_review"] is True
    assert payload["financial_verified"] is False


def test_new_commands_are_registered():
    from stock_robot.cli import main

    for command in ("dividend-discover", "dividend-extract"):
        result = CliRunner().invoke(main, ["radar", command, "--help"])
        assert result.exit_code != 0

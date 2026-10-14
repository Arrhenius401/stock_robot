"""旧研究缓存不能自动变成入池或派息证据。"""

import json

import pytest
from click.testing import CliRunner

from stock_robot.dividend_materials import (
    archive_dividends,
    prepare_candidates,
    prepare_dividends,
)


def test_legacy_per_share_and_total_shares_never_become_actual_amount():
    candidates = [{"symbol": "000333", "current_name": "美的", "annual": [{"year": 2025, "profit": 100}]}]
    styles = [{"symbol": "000333", "observed_three_year_implemented_dividend": True,
               "dividend_events": [{"cash_per10": 38, "TOTAL_SHARES": 100000000}]}]
    result = prepare_candidates(candidates, styles)
    company = result["companies"][0]
    assert company["profits"] == company["events"] == company["coverage"] == []
    assert company["required_share_classes"] == []
    assert company["research_hints"]["dividend_events"][0]["cash_per10"] == 38
    assert result["rule_enabled"] is False


def test_explicit_evidence_is_merged_by_symbol_only():
    result = prepare_candidates([{"symbol": "600900", "name": "长江"}, {"symbol": "000333", "name": "美的"}],
                                evidence=[{"symbol": "600900", "events": [{"event_id": "verified"}]}])
    assert result["companies"][0]["events"] == [{"event_id": "verified"}]
    assert result["companies"][1]["events"] == []


@pytest.mark.parametrize("rows", [[{"symbol": "a"}, {"symbol": "a"}], [{}], "invalid"])
def test_invalid_roster_is_explicit(rows):
    with pytest.raises(ValueError):
        prepare_candidates(rows)


def test_evidence_outside_roster_is_rejected():
    with pytest.raises(ValueError, match="之外"):
        prepare_candidates([{"symbol": "600900"}], evidence=[{"symbol": "000333"}])


def test_prepare_cli_preserves_sources_and_blocks_input_overwrite(tmp_path):
    source = tmp_path / "candidates.json"
    output = tmp_path / "prepared.json"
    source.write_text(json.dumps([{"symbol": "600900", "name": "长江"}]), encoding="utf-8")
    result = CliRunner().invoke(prepare_dividends, ["--candidates", str(source), "--output", str(output)])
    assert result.exit_code == 0, result.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert len(data["input_sources"][0]["sha256"]) == 64
    assert "正式筛选未启用" in result.output
    blocked = CliRunner().invoke(prepare_dividends, ["--candidates", str(source), "--output", str(source)])
    assert blocked.exit_code != 0
    assert json.loads(source.read_text(encoding="utf-8"))[0]["symbol"] == "600900"


def test_archive_cli_uses_collector_without_financial_verdict(tmp_path, monkeypatch):
    import stock_robot.dividend_materials as module

    source = tmp_path / "documents.json"
    output = tmp_path / "result.json"
    source.write_text('{"documents": []}', encoding="utf-8")
    monkeypatch.setattr(module, "collect_documents", lambda *_args, **_kwargs: {"items": []})
    result = CliRunner().invoke(archive_dividends, ["--input", str(source), "--archive-dir", str(tmp_path / "archive"), "--output", str(output)])
    assert result.exit_code == 0, result.output
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["financial_verified"] is False
    assert payload["rule_enabled"] is False


def test_cli_registers_material_commands():
    from stock_robot.cli import main

    for command in ("dividend-prepare", "dividend-archive"):
        result = CliRunner().invoke(main, ["radar", command, "--help"])
        assert result.exit_code == 0, result.output


def test_archive_input_inside_raw_directory_is_rejected(tmp_path):
    archive = tmp_path / 'archive'
    archive.mkdir()
    source = archive / 'manifest.json'
    source.write_text('{"documents": []}', encoding='utf-8')
    result = CliRunner().invoke(archive_dividends, ['--input', str(source), '--archive-dir', str(archive),
                                                  '--output', str(tmp_path / 'result.json')])
    assert result.exit_code != 0
    assert '归档目录之外' in result.output
    assert source.read_text(encoding='utf-8') == '{"documents": []}'
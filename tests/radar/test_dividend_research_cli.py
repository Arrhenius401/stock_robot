"""离线批量入口与缓存隔离验收。"""

import json
from datetime import date

from click.testing import CliRunner

from stock_robot.dividend_research import dividend_research, run_batch


def test_batch_missing_evidence_is_pending_not_failure(tmp_path):
    records = [{"symbol": "600900", "name": "长江电力"}, {"symbol": "000333", "name": "美的集团"}]
    first = run_batch(records, date(2026, 10, 14), tmp_path / "cache")
    assert first["summary"]["pending_verification"] == 2
    assert first["summary"]["meets_reference_condition"] == 0
    assert first["rule_enabled"] is False
    assert run_batch(records, date(2026, 10, 14), tmp_path / "cache")["summary"]["cache_reused"] == 2
    assert run_batch(records, date(2026, 10, 15), tmp_path / "cache")["summary"]["cache_reused"] == 0
    records[0]["name"] = "更新证据"
    assert run_batch(records, date(2026, 10, 14), tmp_path / "cache")["summary"]["cache_reused"] == 1


def test_corrupt_cache_recomputed(tmp_path):
    records = [{"symbol": "600900"}]
    run_batch(records, date(2026, 10, 14), tmp_path)
    cache = next(tmp_path.glob("*.json"))
    cache.write_text('{"result": {"status": "complete"}}', encoding="utf-8")
    result = run_batch(records, date(2026, 10, 14), tmp_path)
    assert result["summary"]["cache_reused"] == 0
    assert result["items"][0]["status"] == "pending_verification"


def test_cli_no_database_or_network_and_atomic_output(tmp_path):
    source = tmp_path / "input.json"
    output = tmp_path / "output.json"
    source.write_text(json.dumps({"companies": [{"symbol": "600900"}]}), encoding="utf-8")
    result = CliRunner().invoke(dividend_research, ["--input", str(source), "--output", str(output), "--as-of", "2026-10-14"])
    assert result.exit_code == 0, result.output
    assert "正式筛选未启用" in result.output
    assert json.loads(output.read_text(encoding="utf-8"))["items"][0]["meets_reference_condition"] is None
    assert sorted(path.name for path in tmp_path.iterdir()) == ["input.json", "output.json"]


def test_invalid_batch_preserves_existing_output(tmp_path):
    source = tmp_path / "input.json"
    output = tmp_path / "output.json"
    output.write_text("旧结果", encoding="utf-8")
    source.write_text(json.dumps({"companies": [{"symbol": "600900"}, {"symbol": "600900"}]}), encoding="utf-8")
    result = CliRunner().invoke(dividend_research, ["--input", str(source), "--output", str(output), "--as-of", "2026-10-14"])
    assert result.exit_code != 0
    assert "重复symbol" in result.output
    assert output.read_text(encoding="utf-8") == "旧结果"


def test_cli_rejects_overwriting_input(tmp_path):
    source = tmp_path / "input.json"
    source.write_text('{"companies": []}', encoding="utf-8")
    result = CliRunner().invoke(dividend_research, ["--input", str(source), "--output", str(source), "--as-of", "2026-10-14"])
    assert result.exit_code != 0
    assert "同一文件" in result.output


def test_main_registers_research_command():
    from stock_robot.cli import main

    result = CliRunner().invoke(main, ["radar", "dividend-research", "--help"])
    assert result.exit_code == 0, result.output
    assert "--as-of" in result.output


def test_official_normalized_fixture_and_batch_isolation(tmp_path):
    from pathlib import Path

    source = Path(__file__).parents[1] / "resources/radar/stock-dividend-evidence.json"
    records = json.loads(source.read_text(encoding="utf-8"))["companies"]
    records.append({"symbol": "000001", "profits": [{"amount_yuan": float("nan")}]})
    result = run_batch(records, date(2026, 10, 14), tmp_path)
    assert result["summary"]["complete"] == 1
    assert result["summary"]["pending_verification"] == 3
    assert result["items"][0]["meets_reference_condition"] is True
    assert result["items"][0]["annual"][0]["dividend_yuan"] == "20063938527.12"
    assert result["items"][-1]["meets_reference_condition"] is None

def test_nonfinite_amount_with_valid_year_does_not_abort_other_companies(tmp_path):
    from copy import deepcopy
    from pathlib import Path

    source = Path(__file__).parents[1] / "resources/radar/stock-dividend-evidence.json"
    good = json.loads(source.read_text(encoding="utf-8"))["companies"][0]
    bad = deepcopy(good)
    bad["symbol"] = "000001"
    bad["profits"][0]["amount_yuan"] = float("nan")
    result = run_batch([bad, good], date(2026, 10, 14), tmp_path / "cache")
    assert result["summary"]["complete"] == 1
    assert result["summary"]["pending_verification"] == 1
    assert result["items"][0]["evidence"]["profits"][0]["amount_yuan"] == "NaN"
    assert result["items"][0]["meets_reference_condition"] is None
    json.dumps(result, allow_nan=False)
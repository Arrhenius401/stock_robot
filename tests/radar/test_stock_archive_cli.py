"""真实归档及成员草案命令往返，不导入外部资格结论。"""

import json
from datetime import datetime

import pytest
from click.testing import CliRunner

from radar.stocks.research_store import StockResearchStore


def run(name, args):
    from stock_robot.cli import main

    result = CliRunner().invoke(main, ["radar", name, *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def prepare(tmp_path):
    db = tmp_path / "archive.db"
    store = StockResearchStore(db)
    today = datetime.now().astimezone().date()
    store.import_companies([{"symbol": "600900", "name": "合成公司"}], source_sha256="a" * 64,
                           source_label="合成候选", data_as_of=today)
    return db, today.isoformat()


def test_capture_replay_detail_and_list(tmp_path):
    db, today = prepare(tmp_path)
    args = ["--db", str(db), "--as-of", today]
    first = run("stock-evaluation-capture", args)
    second = run("stock-evaluation-capture", args)
    assert first["evaluation_id"] == second["evaluation_id"]
    assert first["status"] == "complete"
    listing = run("stock-evaluation-list", ["--db", str(db)])
    assert listing["total"] == 1
    detail = run("stock-evaluation-list", ["--db", str(db), "--evaluation-id", first["evaluation_id"]])
    assert detail["report"]["summary"]["eligible_members"] == 0
    assert detail["inputs"]["companies"][0]["company"]["symbol"] == "600900"


def test_create_cancel_and_export_draft(tmp_path):
    db, today = prepare(tmp_path)
    saved = run("stock-evaluation-capture", ["--db", str(db), "--as-of", today])
    source = tmp_path / "changes.json"
    source.write_text(json.dumps({"evaluation_id": saved["evaluation_id"], "changes": [
        {"pool_id": "dividend_value", "symbol": "600900", "action": "add", "reason": "合成关注"}]}), encoding="utf-8")
    args = ["--db", str(db), "--input", str(source), "--request-id", "cli-1", "--actor", "合成测试"]
    first = run("stock-member-draft-create", args)
    second = run("stock-member-draft-create", args)
    assert first["draft_id"] == second["draft_id"]
    assert first["publication_enabled"] is False
    cancelled = run("stock-member-draft-cancel", ["--db", str(db), "--draft-id", first["draft_id"],
                    "--expected-revision", "1", "--actor", "合成测试", "--reason", "合成撤销"])
    assert cancelled["revision"] == 2
    assert cancelled["status"] == "cancelled"
    listing = run("stock-member-draft-list", ["--db", str(db)])
    assert listing["total"] == 1
    output = tmp_path / "draft.json"
    from stock_robot.cli import main

    result = CliRunner().invoke(main, ["radar", "stock-member-draft-list", "--db", str(db), "--draft-id", first["draft_id"], "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert len(json.loads(output.read_text(encoding="utf-8"))["events"]) == 2


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_archive_output_cannot_cover_database(tmp_path, suffix):
    from stock_robot.cli import main

    db, today = prepare(tmp_path)
    result = CliRunner().invoke(main, ["radar", "stock-evaluation-capture", "--db", str(db), "--as-of", today, "--output", str(db)+suffix])
    assert result.exit_code != 0
    assert "SQLite" in result.output
    assert StockResearchStore(db).summary()["companies"] == 1


def test_no_external_approval_fields_or_input_overwrite(tmp_path):
    from stock_robot.cli import main

    db, _ = prepare(tmp_path)
    source = tmp_path / "changes.json"
    raw = '{"evaluation_id":"' + "a"*64 + '","changes":[],"eligible":true}'
    source.write_text(raw, encoding="utf-8")
    args = ["radar", "stock-member-draft-create", "--db", str(db), "--input", str(source), "--request-id", "bad", "--actor", "测试"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    assert "输入仅允许" in result.output
    rejected = CliRunner().invoke(main, [*args, "--output", str(source)])
    assert rejected.exit_code != 0
    assert "覆盖成员变更输入" in rejected.output
    assert source.read_text(encoding="utf-8") == raw


def test_all_commands_registered_with_lifecycle_commands():
    from stock_robot.cli import main

    result = CliRunner().invoke(main, ["radar", "--help"])
    assert result.exit_code == 0
    for name in ("stock-evaluation-capture", "stock-evaluation-list", "stock-member-draft-create", "stock-member-draft-list", "stock-member-draft-cancel", "stock-membership-apply", "stock-membership-list"):
        assert name in result.output
    assert "stock-member-publish" not in result.output


def test_membership_apply_rejects_unconfigured_screening_without_writing_member(tmp_path):
    from stock_robot.cli import main

    db, today = prepare(tmp_path)
    saved = run("stock-evaluation-capture", ["--db", str(db), "--as-of", today])
    source = tmp_path / "membership.json"
    source.write_text(json.dumps({"evaluation_id": saved["evaluation_id"], "changes": [
        {"pool_id": "dividend_value", "symbol": "600900", "action": "add", "reason": "合成测试"}],
        "expected_pool_revisions": {"dividend_value": 0}}), encoding="utf-8")
    result = CliRunner().invoke(main, ["radar", "stock-membership-apply", "--db", str(db), "--input", str(source),
                                       "--request-id", "member-cli", "--actor", "合成测试"])
    assert result.exit_code != 0
    assert "正式筛选尚未启用" in result.output
    listed = run("stock-membership-list", ["--db", str(db), "--pool-id", "dividend_value"])
    assert listed["total"] == 0 and listed["revision"] == 0


@pytest.mark.parametrize("command", ["stock-member-draft-create", "stock-member-draft-list"])
def test_draft_export_cannot_overwrite_input_pool_configuration(tmp_path, command):
    from stock_robot.cli import main

    db, today = prepare(tmp_path)
    saved = run("stock-evaluation-capture", ["--db", str(db), "--as-of", today])
    directory = tmp_path / "pools"
    directory.mkdir()
    config = directory / "dividend_value.yaml"
    raw = "id: dividend_value\nname: 合成池\ndescription: 合成配置\ndisplay_order: 0\n"
    config.write_text(raw, encoding="utf-8")
    args = ["radar", command, "--db", str(db), "--pool-dir", str(directory), "--output", str(config)]
    if command.endswith("create"):
        source = tmp_path / "changes.json"
        source.write_text(json.dumps({"evaluation_id": saved["evaluation_id"], "changes": [
            {"pool_id": "dividend_value", "symbol": "600900", "action": "add", "reason": "合成测试"}]}), encoding="utf-8")
        args += ["--input", str(source), "--actor", "测试", "--request-id", "guard"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    assert "股票池配置目录" in result.output
    assert config.read_text(encoding="utf-8") == raw

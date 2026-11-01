"""报告保留策略的边界契约：只删除已验证且过期的完整报告单元。"""
import hashlib
import json
from datetime import UTC, datetime

from maintenance.report_retention import RETENTION_DAYS, sweep

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC).timestamp()
LOCAL = datetime.now().astimezone().tzinfo
DAYS = RETENTION_DAYS


def make_report(
    root,
    category,
    symbol,
    when,
    *,
    snapshot=True,
    name=None,
    content="# 报告\n",
):
    month = root / category / symbol / when.strftime("%Y-%m")
    month.mkdir(parents=True, exist_ok=True)
    filename = name or f"{symbol}_{when.strftime('%Y%m%d_%H%M%S')}.md"
    path = month / filename
    path.write_bytes(content.encode("utf-8"))
    if snapshot:
        write_snapshot(path, content)
    return path


def write_snapshot(path, content="# 报告\n"):
    wrapper = {
        "schema_version": 1,
        "markdown_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "payload": {},
    }
    path.with_suffix(".report.json").write_bytes(
        json.dumps(wrapper).encode("utf-8")
    )
    return path.with_suffix(".report.json")


def expired():
    return datetime(2026, 10, 1, 9, 0, tzinfo=LOCAL)


def test_retention_is_fixed_at_seven_days():
    """保留期是固定策略，不提供按天数的配置项。"""
    assert RETENTION_DAYS == 7


def test_expired_unit_removes_markdown_and_snapshot(tmp_path):
    root = tmp_path / "reports"
    path = make_report(root, "stock", "600519", expired())
    snapshot = path.with_suffix(".report.json")

    result = sweep(root, enabled=True, now=NOW)

    assert not path.exists() and not snapshot.exists()
    assert result.scanned == 1
    assert result.deleted == 1
    assert result.freed_bytes > 0
    assert result.failures == 0
    assert not (root / "stock" / "600519").exists()


def test_recent_and_boundary_reports_are_kept(tmp_path):
    root = tmp_path / "reports"
    recent = make_report(root, "gold", "GOLD", datetime(2026, 10, 30, 9, 0, tzinfo=LOCAL))
    boundary = make_report(
        root, "index", "000300",
        datetime.fromtimestamp(NOW - DAYS * 86400, tz=LOCAL),
    )

    result = sweep(root, enabled=True, now=NOW)

    assert recent.exists() and boundary.exists()
    assert result.deleted == 0
    assert result.scanned == 2


def test_one_second_past_boundary_is_deleted(tmp_path):
    root = tmp_path / "reports"
    path = make_report(
        root, "stock", "000001",
        datetime.fromtimestamp(NOW - DAYS * 86400 - 1, tz=LOCAL),
    )

    result = sweep(root, enabled=True, now=NOW)

    assert not path.exists()
    assert result.deleted == 1


def test_disabled_policy_performs_no_file_operations(tmp_path):
    root = tmp_path / "reports"
    path = make_report(root, "stock", "600519", expired())

    result = sweep(root, enabled=False, now=NOW)

    assert path.exists()
    assert result.deleted == 0 and result.scanned == 0


def test_missing_reports_root_is_ignored(tmp_path):
    result = sweep(tmp_path / "missing", enabled=True, now=NOW)
    assert result.deleted == 0 and result.scanned == 0


def test_snapshot_digest_mismatch_keeps_the_unit(tmp_path):
    root = tmp_path / "reports"
    path = make_report(root, "stock", "600519", expired())
    path.with_suffix(".report.json").write_text("{}", encoding="utf-8")

    result = sweep(root, enabled=True, now=NOW)

    assert path.exists() and path.with_suffix(".report.json").exists()
    assert result.deleted == 0
    assert result.skipped == 1


def test_unknown_files_are_kept_and_block_directory_removal(tmp_path):
    root = tmp_path / "reports"
    path = make_report(root, "index", "000300", expired())
    month = path.parent
    unknown = month / "user-report.md"
    unknown.write_text("手工保存", encoding="utf-8")

    result = sweep(root, enabled=True, now=NOW)

    assert not path.exists()
    assert unknown.exists()
    assert month.is_dir()
    assert result.deleted == 1


def test_legacy_root_stock_layout_is_cleaned(tmp_path):
    root = tmp_path / "reports"
    month = root / "600519" / "2026-10"
    month.mkdir(parents=True)
    path = month / "600519_20261001_120000.md"
    path.write_text("旧版报告", encoding="utf-8")

    result = sweep(root, enabled=True, now=NOW)

    assert not path.exists()
    assert not (root / "600519").exists()
    assert result.deleted == 1


def test_index_uuid_and_namespaced_symbol_are_recognized(tmp_path):
    root = tmp_path / "reports"
    uuid_name = "000300_20261001_120000_0123456789abcdef0123456789abcdef.md"
    plain = make_report(root, "index", "000300", expired(), name=uuid_name)
    namespaced = make_report(
        root, "index", "~616263", expired(),
        name="~616263_20261001_120000_0123456789abcdef0123456789abcdef.md",
    )

    result = sweep(root, enabled=True, now=NOW)

    assert not plain.exists() and not namespaced.exists()
    assert result.deleted == 2


def test_mismatched_symbol_and_month_are_kept(tmp_path):
    root = tmp_path / "reports"
    wrong_symbol = make_report(
        root, "stock", "600519", expired(), name="600000_20261001_120000.md"
    )
    month = root / "stock" / "600519" / "2026-11"
    month.mkdir()
    mismatched = month / "600519_20261001_120000.md"
    mismatched.write_text("报告", encoding="utf-8")

    result = sweep(root, enabled=True, now=NOW)

    assert wrong_symbol.exists() and mismatched.exists()
    assert result.deleted == 0


def test_gold_and_non_gold_symbols(tmp_path):
    root = tmp_path / "reports"
    uuid_name = "GOLD_20261001_120000_0123456789abcdef0123456789abcdef.md"
    gold = make_report(root, "gold", "GOLD", expired(), name=uuid_name)
    silver = make_report(root, "gold", "SILVER", expired(), name="SILVER_20261001_120000.md")

    result = sweep(root, enabled=True, now=NOW)

    assert not gold.exists()
    assert silver.exists()
    assert result.deleted == 1


def test_orphan_snapshot_is_removed_after_retention(tmp_path):
    root = tmp_path / "reports"
    month = root / "index" / "000300" / "2026-10"
    month.mkdir(parents=True)
    snapshot = month / (
        "000300_20261001_120000_0123456789abcdef0123456789abcdef.report.json"
    )
    snapshot.write_text("{}", encoding="utf-8")

    result = sweep(root, enabled=True, now=NOW)

    assert not snapshot.exists()
    assert result.orphans == 1
    assert result.deleted == 0
    assert not month.exists()


def test_future_and_invalid_timestamps_are_kept(tmp_path):
    root = tmp_path / "reports"
    future = make_report(
        root, "stock", "600519", datetime(2026, 11, 10, 9, 0, tzinfo=LOCAL)
    )
    invalid = make_report(
        root, "stock", "600519", expired(), name="600519_20261332_120000.md"
    )

    result = sweep(root, enabled=True, now=NOW)

    assert future.exists() and invalid.exists()
    assert result.deleted == 0


def test_unknown_filename_is_ignored(tmp_path):
    root = tmp_path / "reports"
    month = root / "stock" / "600519" / "2026-10"
    month.mkdir(parents=True)
    unknown = month / "600519_report.md"
    unknown.write_text("报告", encoding="utf-8")

    result = sweep(root, enabled=True, now=NOW)

    assert unknown.exists()
    assert result.scanned == 0

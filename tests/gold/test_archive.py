"""黄金报告存档、快照回退与路径边界回归。"""

import base64
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from api.report_library import (
    ReportLibraryError,
    get_report_detail,
    list_reports,
    resolve_download_path,
)
from report.formatter import ReportFormatter


def _id(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


def _write_gold(root: Path) -> tuple[Path, dict]:
    path = root / "gold" / "GOLD" / "2026-10" / "GOLD_20261026_120000_sample.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "# 黄金综合研报\n\n长期：看多\n"
    path.write_bytes(text.encode("utf-8"))
    payload = {
        "report_type": "gold",
        "symbol": "GOLD",
        "long_term": {"direction": "看多"},
        "markets": {"domestic": {"short_term": {"direction": "偏多"}}},
        "markdown": text,
    }
    wrapper = {
        "schema_version": 1,
        "markdown_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "payload": payload,
    }
    path.with_suffix(".report.json").write_text(json.dumps(wrapper), encoding="utf-8")
    return path, payload


def test_gold_save_uses_fixed_identity_and_never_overwrites_same_second(
    tmp_path, monkeypatch
):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 26, 12, tzinfo=tz)

    monkeypatch.setattr("report.formatter.datetime", FixedDatetime)
    first = ReportFormatter.save("第一份", "GOLD", tmp_path, category="gold")
    second = ReportFormatter.save("第二份", "GOLD", tmp_path, category="gold")
    assert first.parent == tmp_path / "gold" / "GOLD" / "2026-10"
    assert first != second
    assert first.name.startswith("GOLD_20261026_120000_")
    assert first.read_text(encoding="utf-8") == "第一份"
    assert second.read_text(encoding="utf-8") == "第二份"


@pytest.mark.parametrize("symbol", ["gold", "XAU", "../GOLD", "GOLD/other", "000001"])
def test_gold_save_rejects_noncanonical_symbols(tmp_path, symbol):
    with pytest.raises(ValueError):
        ReportFormatter.save("正文", symbol, tmp_path, category="gold")


def test_gold_library_lists_reads_snapshot_and_downloads_original_markdown(tmp_path):
    path, payload = _write_gold(tmp_path)
    reports = list_reports(tmp_path, report_type="gold")
    assert len(reports) == 1
    summary = reports[0]
    assert summary.type == "gold" and summary.symbol == "GOLD"
    assert "黄金" in summary.title
    assert list_reports(tmp_path, report_type="stock") == []
    detail = get_report_detail(tmp_path, summary.id)
    assert detail.gold_payload == payload
    assert detail.stock_payload is None
    assert detail.to_dict()["gold_payload"] == payload
    assert resolve_download_path(tmp_path, summary.id) == path.resolve()
    assert detail.markdown == path.read_text(encoding="utf-8")


def test_gold_snapshot_matches_actual_crlf_bytes_without_normalizing_digest(tmp_path):
    path, payload = _write_gold(tmp_path)
    text = "# 黄金综合研报\r\n\r\n原始CRLF正文\r\n"
    path.write_bytes(text.encode("utf-8"))
    wrapper = {
        "schema_version": 1,
        "markdown_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "payload": payload,
    }
    path.with_suffix(".report.json").write_text(json.dumps(wrapper), encoding="utf-8")
    detail = get_report_detail(tmp_path, _id(path.relative_to(tmp_path).as_posix()))
    assert detail.gold_payload == payload
    assert detail.markdown == text


@pytest.mark.parametrize(
    "failure", ["missing", "corrupt", "hash", "symbol", "nonfinite", "version"]
)
def test_gold_snapshot_failure_retains_original_markdown(tmp_path, failure):
    path, _ = _write_gold(tmp_path)
    snapshot = path.with_suffix(".report.json")
    wrapper = json.loads(snapshot.read_text(encoding="utf-8"))
    if failure == "missing":
        snapshot.unlink()
    elif failure == "corrupt":
        snapshot.write_text("{broken", encoding="utf-8")
    else:
        if failure == "hash":
            wrapper["markdown_sha256"] = "wrong"
        elif failure == "symbol":
            wrapper["payload"]["symbol"] = "XAU"
        elif failure == "nonfinite":
            wrapper["payload"]["long_term"]["value"] = float("nan")
        elif failure == "version":
            wrapper["schema_version"] = 99
        snapshot.write_text(json.dumps(wrapper), encoding="utf-8")
    summary = list_reports(tmp_path, report_type="gold")[0]
    detail = get_report_detail(tmp_path, summary.id)
    assert detail.gold_payload is None
    assert detail.markdown == path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "relative",
    [
        "gold/XAU/2026-10/foreign.md",
        "gold/GOLD/2026-10/nested/hidden.md",
        "gold/GOLD/bad-month/file.md",
    ],
)
def test_gold_library_rejects_unknown_gold_shapes(tmp_path, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 不应识别", encoding="utf-8")
    assert list_reports(tmp_path, report_type="gold") == []
    with pytest.raises(ReportLibraryError):
        resolve_download_path(tmp_path, _id(relative))


def test_gold_snapshot_path_is_checked_before_loading(tmp_path, monkeypatch):
    import api.report_library as library

    path, _ = _write_gold(tmp_path)
    real_safe_relative = library._safe_relative
    monkeypatch.setattr(
        library,
        "_safe_relative",
        lambda root, target: (
            None if target.suffix == ".json" else real_safe_relative(root, target)
        ),
    )
    monkeypatch.setattr(
        "gold.service.load_gold_snapshot", lambda *_: pytest.fail("越界快照不应读取")
    )
    detail = get_report_detail(tmp_path, _id(path.relative_to(tmp_path).as_posix()))
    assert detail.gold_payload is None
    assert "长期：看多" in detail.markdown

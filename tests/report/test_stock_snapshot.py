"""结构化存档与 Markdown 同批次、边界和兼容性。"""
import hashlib
import json
from datetime import UTC

import pytest

from api.report_library import get_report_detail, list_reports
from report.formatter import ReportFormatter


def payload(symbol="000001"):
    return {"symbol": symbol, "name": "样本", "overview": {"latest_close": 10},
            "score": {"final": 8}, "dimensions": {"financial": {"metrics": {"roe": .12}}},
            "commentary_retry_id": "temporary-secret"}


def test_save_snapshot_roundtrip_and_token_numeric_cleanup(tmp_path):
    data = payload()
    data["overview"]["unknown"] = float("nan")
    path = ReportFormatter.save("# 样本\n完整原文", "000001", output_dir=tmp_path, stock_payload=data)
    wrapper = json.loads(path.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert wrapper["schema_version"] == 1
    assert wrapper["markdown_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "commentary_retry_id" not in wrapper["payload"]
    assert wrapper["payload"]["overview"]["unknown"] is None
    assert data["commentary_retry_id"] == "temporary-secret"
    summary = list_reports(tmp_path)[0]
    detail = get_report_detail(tmp_path, summary.id)
    assert detail.stock_payload == wrapper["payload"]
    assert detail.markdown == "# 样本\n完整原文"


def test_hk_stock_snapshot_roundtrip(tmp_path):
    path = ReportFormatter.save(
        "# 港股样本\n完整原文", "00700.HK", output_dir=tmp_path,
        stock_payload=payload("00700.HK"),
    )

    assert path.parent.parent.name == "00700.HK"
    detail = get_report_detail(tmp_path, list_reports(tmp_path)[0].id)
    assert detail.stock_payload is not None
    assert detail.stock_payload["symbol"] == "00700.HK"


@pytest.mark.parametrize("fault", ["missing", "malformed", "digest", "symbol", "version", "shape", "nonfinite"])
def test_snapshot_fault_falls_back_to_complete_markdown(tmp_path, fault, caplog):
    path = ReportFormatter.save("# 样本\n完整原文", "000001", output_dir=tmp_path, stock_payload=payload())
    snapshot = path.with_suffix(".report.json")
    wrapper = json.loads(snapshot.read_text(encoding="utf-8"))
    if fault == "missing":
        snapshot.unlink()
    elif fault == "malformed":
        snapshot.write_text("{broken", encoding="utf-8")
    else:
        if fault == "digest":
            wrapper["markdown_sha256"] = "0" * 64
        elif fault == "symbol":
            wrapper["payload"]["symbol"] = "600519"
        elif fault == "version":
            wrapper["schema_version"] = 2
        elif fault == "nonfinite":
            wrapper["payload"]["score"]["final"] = float("inf")
        else:
            wrapper["payload"]["dimensions"] = []
        snapshot.write_text(json.dumps(wrapper), encoding="utf-8")
    detail = get_report_detail(tmp_path, list_reports(tmp_path)[0].id)
    assert detail.stock_payload is None
    assert detail.markdown == "# 样本\n完整原文"
    if fault != "missing":
        assert "快照" in caplog.text


def test_retry_updates_markdown_and_snapshot_same_path(tmp_path):
    path = ReportFormatter.save("# 旧解读", "000001", output_dir=tmp_path, stock_payload=payload())
    updated = payload()
    updated["commentary"] = "新解读"
    ReportFormatter.update_stock_snapshot("# 新解读", path, updated)
    detail = get_report_detail(tmp_path, list_reports(tmp_path)[0].id)
    assert detail.markdown == "# 新解读"
    assert detail.stock_payload is not None
    assert detail.stock_payload["commentary"] == "新解读"


def test_index_and_legacy_markdown_are_unchanged(tmp_path):
    path = ReportFormatter.save("# 指数正文", "000300", output_dir=tmp_path, category="index", stock_payload=payload("000300"))
    assert not path.with_suffix(".report.json").exists()
    detail = get_report_detail(tmp_path, list_reports(tmp_path)[0].id)
    assert detail.stock_payload is None
    assert detail.markdown == "# 指数正文"


def test_save_rejects_symbol_path_escape(tmp_path):
    with pytest.raises(ValueError):
        ReportFormatter.save("# 内容", "../outside", output_dir=tmp_path, stock_payload=payload())
    assert not (tmp_path.parent / "outside").exists()


def test_crlf_digest_matches_actual_returned_markdown(tmp_path):
    text = "# 样本\r\n完整原文\r\n"
    ReportFormatter.save(text, "000001", output_dir=tmp_path, stock_payload=payload())
    detail = get_report_detail(tmp_path, list_reports(tmp_path)[0].id)
    assert detail.markdown == text
    assert detail.stock_payload is not None


def test_concurrent_update_cannot_pair_old_markdown_with_new_payload(tmp_path, monkeypatch):
    import api.report_library as library

    path = ReportFormatter.save("# 旧原文", "000001", output_dir=tmp_path, stock_payload=payload())
    summary = list_reports(tmp_path)[0]
    original = library._read_text

    reads = 0

    def read_then_update(target):
        nonlocal reads
        reads += 1
        text = original(target)
        if reads == 2:
            ReportFormatter.update_stock_snapshot("# 新原文", path, payload())
        return text

    monkeypatch.setattr(library, "_read_text", read_then_update)
    detail = get_report_detail(tmp_path, summary.id)
    assert detail.markdown == "# 旧原文"
    assert detail.stock_payload is None


def test_snapshot_outside_root_does_not_read_external_json(tmp_path, monkeypatch):
    import api.report_library as library

    ReportFormatter.save("# 原文", "000001", output_dir=tmp_path, stock_payload=payload())
    original = library._safe_relative
    monkeypatch.setattr(library, "_safe_relative", lambda root, target: None if target.suffix == ".json" else original(root, target))
    detail = get_report_detail(tmp_path, list_reports(tmp_path)[0].id)
    assert detail.stock_payload is None
    assert detail.markdown == "# 原文"


def test_snapshot_write_failure_retains_markdown(tmp_path, monkeypatch):
    from report import formatter

    original = formatter._atomic_write

    def fail_json(path, content):
        if path.suffix == ".json":
            raise OSError("模拟写入失败")
        return original(path, content)

    monkeypatch.setattr(formatter, "_atomic_write", fail_json)
    path = ReportFormatter.save("# 完整原文", "000001", output_dir=tmp_path, stock_payload=payload())
    assert path.read_text(encoding="utf-8") == "# 完整原文"
    assert get_report_detail(tmp_path, list_reports(tmp_path)[0].id).stock_payload is None


def test_update_refuses_non_stock_path_and_symbol_mismatch(tmp_path):
    path = tmp_path / "outside.md"
    path.write_text("原文", encoding="utf-8")
    with pytest.raises(ValueError):
        ReportFormatter.update_stock_snapshot("新原文", path, payload())
    assert path.read_text(encoding="utf-8") == "原文"
    saved = ReportFormatter.save("# 原文", "000001", output_dir=tmp_path, stock_payload=payload())
    with pytest.raises(ValueError):
        ReportFormatter.update_stock_snapshot("新原文", saved, payload("600519"))
    assert saved.read_text(encoding="utf-8") == "# 原文"


def test_safe_overseas_index_code_keeps_existing_save_behavior(tmp_path):
    path = ReportFormatter.save("# 海外指数", "SPX", output_dir=tmp_path, category="index")
    assert path.parent.parent.name == "SPX"
    assert path.read_text(encoding="utf-8") == "# 海外指数"
    assert not path.with_suffix(".report.json").exists()


def test_snapshot_resolve_failure_is_nonfatal(tmp_path, monkeypatch):
    import api.report_library as library

    ReportFormatter.save("# 完整原文", "000001", output_dir=tmp_path, stock_payload=payload())
    original = library._safe_relative

    def resolve_error(root, target):
        if target.suffix == ".json":
            raise RuntimeError("Symlink loop")
        return original(root, target)

    monkeypatch.setattr(library, "_safe_relative", resolve_error)
    detail = get_report_detail(tmp_path, list_reports(tmp_path)[0].id)
    assert detail.stock_payload is None
    assert detail.markdown == "# 完整原文"


@pytest.mark.parametrize("invalid_payload", [False, True])
def test_same_second_markdown_only_does_not_reuse_prior_snapshot(tmp_path, monkeypatch, invalid_payload):
    from datetime import datetime

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 12, 9, 0, tzinfo=UTC)

    monkeypatch.setattr("report.formatter.datetime", FixedDatetime)
    path = ReportFormatter.save("# 完整原文", "000001", output_dir=tmp_path, stock_payload=payload())
    assert path.with_suffix(".report.json").exists()
    ReportFormatter.save("# 完整原文", "000001", output_dir=tmp_path, stock_payload={} if invalid_payload else None)
    detail = get_report_detail(tmp_path, list_reports(tmp_path)[0].id)
    assert detail.stock_payload is None
    assert detail.markdown == "# 完整原文"


def test_update_json_failure_keeps_new_markdown_and_falls_back(tmp_path, monkeypatch, caplog):
    from report import formatter

    path = ReportFormatter.save("# 旧原文", "000001", output_dir=tmp_path, stock_payload=payload())
    original = formatter._atomic_write

    def fail_json(path, content):
        if path.suffix == ".json":
            raise OSError("模拟快照不可写")
        original(path, content)

    monkeypatch.setattr(formatter, "_atomic_write", fail_json)
    ReportFormatter.update_stock_snapshot("# 新原文", path, payload())
    detail = get_report_detail(tmp_path, list_reports(tmp_path)[0].id)
    assert detail.markdown == "# 新原文"
    assert detail.stock_payload is None
    assert "快照保存失败" in caplog.text


def test_update_markdown_failure_propagates_without_changing_snapshot(tmp_path, monkeypatch):
    from report import formatter

    path = ReportFormatter.save("# 原文", "000001", output_dir=tmp_path, stock_payload=payload())
    previous = path.with_suffix(".report.json").read_bytes()

    def fail_write(path, content):
        raise OSError("模拟原文不可写")

    monkeypatch.setattr(formatter, "_atomic_write", fail_write)
    with pytest.raises(OSError):
        ReportFormatter.update_stock_snapshot("# 不应出现", path, payload())
    assert path.read_text(encoding="utf-8") == "# 原文"
    assert path.with_suffix(".report.json").read_bytes() == previous

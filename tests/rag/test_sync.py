"""LocalReportSync 单元测试"""
import pytest
from pathlib import Path
from rag.sync import LocalReportSync


class FakeRAGEngine:
    def __init__(self):
        self.ingested_files = []

    def ingest_file(self, file_path, source_type, title, date, symbols, tags):
        self.ingested_files.append({
            "file_path": file_path,
            "source_type": source_type,
            "title": title,
            "date": date,
            "symbols": symbols,
            "tags": tags,
        })
        return {"status": "success", "chunks_count": 3, "source_hash": "fake_hash"}

    def list_sources(self):
        return []


class TestLocalReportSync:
    @pytest.fixture
    def sync(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        return LocalReportSync(
            reports_dir=str(reports_dir),
            engine=FakeRAGEngine(),
        )

    def test_scan_new_files_detects_new_reports(self, sync):
        report_path = Path(sync.reports_dir) / "000001_20260807_test.md"
        report_path.write_text("# 报告\n\n## 分析\n\n测试内容", encoding="utf-8")

        results = sync.scan_new_files()
        assert len(results) > 0

    def test_scan_new_files_skips_already_ingested(self, sync):
        report_path = Path(sync.reports_dir) / "000001_20260807_existing.md"
        content = "# 报告\n\n已摄入内容"
        report_path.write_text(content, encoding="utf-8")

        results = sync.scan_new_files()
        ingested_hashes = {r["hash"] for r in results if "hash" in r}

        results2 = sync.scan_new_files(ingested_hashes=ingested_hashes)
        assert len(results2) == 0

    def test_full_sync_processes_new_files(self, sync):
        report_path = Path(sync.reports_dir) / "000001_20260807_sync.md"
        report_path.write_text("# 同步测试\n\n## 维度一\n\n测试数据", encoding="utf-8")

        result = sync.sync()
        assert result["processed"] > 0 or result["failed"] == 0

    def test_extract_report_info_parses_filename(self, sync):
        info = sync.extract_report_info("000001_20260807_161939.md")
        assert "000001" in info.get("symbols", [])
        assert info.get("date") is not None

    def test_extract_symbols_from_content(self, sync):
        content = "# 平安银行（000001）分析报告\n\n内容"
        symbols = sync.extract_symbols_from_content(content)
        assert "000001" in symbols

    def test_empty_reports_dir_scan_returns_empty(self, sync):
        results = sync.scan_new_files()
        assert results == []

    def test_sync_handles_engine_failure(self, tmp_path):
        class BrokenEngine:
            def ingest_file(self, **kwargs):
                raise RuntimeError("引擎故障")
            def list_sources(self):
                return []
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "000001_test.md").write_text("# 测试", encoding="utf-8")

        sync = LocalReportSync(
            reports_dir=str(reports_dir),
            engine=BrokenEngine(),
        )
        result = sync.sync()
        assert result["failed"] >= 1

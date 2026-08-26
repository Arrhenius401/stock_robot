from datetime import datetime

from report.formatter import ReportFormatter, metric_display_name


class TestReportFormatter:
    def test_metric_display_name_translates_known_key_and_keeps_unknown_key(self):
        assert metric_display_name("latest_quarter") == "最新财报季度"
        assert metric_display_name("pe_ttm") == "PE(TTM)"
        assert metric_display_name("new_vendor_metric") == "new_vendor_metric"

    def test_save_to_file(self, tmp_path):
        report = "# 测试报告\n内容"
        saved_path = ReportFormatter.save(report, "000001", output_dir=tmp_path)
        assert saved_path.exists()
        content = saved_path.read_text(encoding="utf-8")
        assert "测试报告" in content
        assert saved_path.name.startswith("000001_")

    def test_save_groups_reports_by_symbol_and_year_month(self, tmp_path, monkeypatch):
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 8, 26, 9, 30, tzinfo=tz)

        monkeypatch.setattr("report.formatter.datetime", FixedDatetime)
        saved_path = ReportFormatter.save("# 测试报告", "000001", output_dir=tmp_path)

        assert saved_path == (tmp_path / "000001" / "2026-08"
                              / "000001_20260826_093000.md")

    def test_to_rich_markdown(self):
        report = "# 标题\n**加粗**\n- 列表项"
        md = ReportFormatter.to_rich_markdown(report)
        assert md is not None

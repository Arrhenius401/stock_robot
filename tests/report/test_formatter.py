from pathlib import Path
from src.report.formatter import ReportFormatter


class TestReportFormatter:
    def test_save_to_file(self, tmp_path):
        report = "# 测试报告\n内容"
        saved_path = ReportFormatter.save(report, "000001", output_dir=tmp_path)
        assert saved_path.exists()
        content = saved_path.read_text(encoding="utf-8")
        assert "测试报告" in content
        assert saved_path.name.startswith("000001_")

    def test_to_rich_markdown(self):
        report = "# 标题\n**加粗**\n- 列表项"
        md = ReportFormatter.to_rich_markdown(report)
        assert md is not None

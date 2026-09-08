"""CLI index 命令测试"""
from types import SimpleNamespace

from click.testing import CliRunner

from src.stock_robot.cli import main


class TestIndexCommand:
    def test_index_pipeline_progress_callback_is_supported(self, mocker):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        mapping = mocker.patch("data.index_mapping.IndexMapping")
        mapping.return_value.lookup.return_value = None
        mocker.patch("index.pipeline.IndexPipeline.run", side_effect=self._run_with_progress)

        result = CliRunner().invoke(main, ["index", "000300", "--style", "broad"])

        assert result.exit_code == 0

    @staticmethod
    def _run_with_progress(targets, on_progress):
        on_progress("data", 1, 3, "读取")

        class Result:
            reports = []
            compare = None
            errors = []

        return Result()

    def test_index_command_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["index", "--help"])
        assert result.exit_code == 0
        assert "SYMBOLS" in result.output or "symbols" in result.output

    def test_index_single_symbol(self):
        runner = CliRunner()
        result = runner.invoke(main, ["index", "000300", "--style", "broad"])
        assert result.exit_code in (0, 1)

    def test_index_markdown_output_saves_under_index_category(self, mocker, tmp_path):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        mocker.patch("data.index_mapping.IndexMapping.lookup", return_value=None)
        mocker.patch("index.pipeline.IndexPipeline.run", return_value=SimpleNamespace(
            reports=[SimpleNamespace(code="000300", name="测试指数")],
            compare=None,
            errors=[],
        ))
        mocker.patch("stock_robot.cli._render_index_report_md", return_value="# 指数报告")
        mocker.patch("src.stock_robot.cli._render_index_report_md", return_value="# 指数报告")
        mock_save = mocker.patch(
            "report.formatter.ReportFormatter.save",
            return_value=tmp_path / "index" / "000300" / "2026-08" / "000300.md",
        )

        result = CliRunner().invoke(
            main, ["index", "000300", "--style", "broad", "--output", "markdown"]
        )

        assert result.exit_code == 0
        assert mock_save.call_args.kwargs["category"] == "index"

    def test_index_invalid_symbol(self):
        runner = CliRunner()
        result = runner.invoke(main, ["index", "abc"])
        assert result.exit_code == 1

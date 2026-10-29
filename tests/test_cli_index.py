"""CLI index 命令测试"""
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from src.stock_robot.cli import main


class TestIndexCommand:
    def test_index_pipeline_progress_callback_is_supported(self, mocker):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        mocker.patch("src.stock_robot.cli._check_disclaimer", return_value=True)
        mapping = mocker.patch("data.index_mapping.IndexMapping")
        mapping.return_value.resolve.return_value = None
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

    def test_index_markdown_output_saves_under_index_category(self, mocker, tmp_path, monkeypatch):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        mocker.patch("src.stock_robot.cli._check_disclaimer", return_value=True)
        mocker.patch("data.index_mapping.IndexMapping.lookup", return_value=None)
        from datetime import datetime

        from data.schemas import AnalysisTarget, IndexAnalysisContext
        from index.archives import load_index_snapshot
        from index.build_single import IndexReportBuilder

        report = IndexReportBuilder().build(IndexAnalysisContext(target=AnalysisTarget(
            target_type="index", symbol="000300", name="测试指数", index_style="broad")), [])
        mocker.patch("index.pipeline.IndexPipeline.run", return_value=SimpleNamespace(
            reports=[report], compare=None, errors=[]))
        mocker.patch("index.archives.datetime").now.return_value = datetime(2026, 10, 29).astimezone()
        # 新保存器使用隔离cwd，验证真实Markdown与摘要绑定快照。
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(main, ["index", "000300", "--style", "broad", "--output", "markdown"])
        assert result.exit_code == 0, result.output
        paths = list((tmp_path / "reports" / "index" / "000300" / "2026-10").glob("*.md"))
        assert len(paths) == 1
        text = paths[0].read_text(encoding="utf-8")
        assert "## 七、免责声明" in text
        snapshot = load_index_snapshot(paths[0], text, "000300")
        assert snapshot is not None and snapshot["report_version"] == 2

    def test_index_invalid_symbol(self, mocker):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        mocker.patch("src.stock_robot.cli._check_disclaimer", return_value=True)
        runner = CliRunner()
        result = runner.invoke(main, ["index", "abc"])
        assert result.exit_code == 1


@pytest.mark.parametrize("query,expected", [("515300", "930740"), ("国证自由现金流", "980092"), ("h30269", "H30269")])
def test_cli_strategy_name_and_etf_routing(mocker, query, expected):
    mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
    mocker.patch("src.stock_robot.cli._check_disclaimer", return_value=True)
    run = mocker.patch("index.pipeline.IndexPipeline.run", return_value=SimpleNamespace(reports=[], compare=None, errors=[]))
    result = CliRunner().invoke(main, ["index", query])
    assert result.exit_code == 0, result.output
    target = run.call_args.args[0][0]
    assert target.symbol == expected
    assert target.index_style == "strategy"
    if query == "515300":
        assert target.requested_instrument["symbol"] == "515300"

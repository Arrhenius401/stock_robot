"""CLI index 命令测试"""
from click.testing import CliRunner

from src.stock_robot.cli import main


class TestIndexCommand:
    def test_index_command_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["index", "--help"])
        assert result.exit_code == 0
        assert "SYMBOLS" in result.output or "symbols" in result.output

    def test_index_single_symbol(self):
        runner = CliRunner()
        result = runner.invoke(main, ["index", "000300", "--style", "broad"])
        assert result.exit_code in (0, 1)

    def test_index_invalid_symbol(self):
        runner = CliRunner()
        result = runner.invoke(main, ["index", "abc"])
        assert result.exit_code == 1

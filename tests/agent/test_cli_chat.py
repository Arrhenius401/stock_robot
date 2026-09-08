"""CLI chat 命令集成测试"""
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from stock_robot.cli import main


class TestChatCommand:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_chat_command_exists(self, runner):
        result = runner.invoke(main, ["chat", "--help"])
        assert result.exit_code == 0
        assert "对话" in result.output

    def test_chat_ask_single_shot(self, runner, mocker):
        """单次对话模式 —— 验证 --ask 选项被接受"""
        mocker.patch(
            "api.bootstrap.build_agent_core",
            return_value=SimpleNamespace(llm=None, registry=object(), model=None),
        )
        mocker.patch("agent.planner.Planner")
        mocker.patch("agent.executor.Executor")
        mocker.patch("agent.chat.ChatResponder")
        mock_run = mocker.patch("stock_robot.cli._run_agent_query")
        result = runner.invoke(main, ["chat", "--ask", "什么是PE"])

        assert result.exit_code == 0
        mock_run.assert_called_once()

    def test_chat_verbose_flag_accepted(self, runner):
        result = runner.invoke(main, ["chat", "--help"])
        assert result.exit_code == 0
        assert "--verbose" in result.output

    def test_analyze_command_still_works(self, runner):
        """存量命令不受影响"""
        result = runner.invoke(main, ["analyze", "--help"])
        assert result.exit_code == 0
        assert "analyze" in result.output.lower() or "分析" in result.output

    def test_index_command_still_works(self, runner):
        """存量命令不受影响"""
        result = runner.invoke(main, ["index", "--help"])
        assert result.exit_code == 0
        assert "index" in result.output.lower() or "指数" in result.output

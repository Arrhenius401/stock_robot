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
        assert "输入 /help" not in result.output
        mock_run.assert_called_once()

    def test_interactive_startup_distinguishes_chat_and_terminal_help(self, runner, mocker):
        mocker.patch("api.bootstrap.build_agent_core",
                     return_value=SimpleNamespace(llm=None, registry=object(), model=None))
        planner = mocker.patch("agent.planner.Planner").return_value
        executor = mocker.patch("agent.executor.Executor").return_value
        responder = mocker.patch("agent.chat.ChatResponder").return_value
        mocker.patch("agent.memory.Memory")
        result = runner.invoke(main, ["chat"], input="/help\n/exit\n")
        assert result.exit_code == 0, result.output
        assert "输入 /help 查看聊天命令" in result.output
        assert "终端命令帮助：stock-robot help" in result.output
        assert "可用快捷指令" in result.output
        planner.plan.assert_not_called()
        executor.execute.assert_not_called()
        responder.reply.assert_not_called()

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

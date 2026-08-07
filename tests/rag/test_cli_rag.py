"""CLI rag 命令集成测试

由于本机 chromadb 无法正常运行（hnswlib segfault），所有涉及 RAGEngine
初始化的测试均通过 mock 绕过。
"""
import pytest
from click.testing import CliRunner


class TestCLIRagGroup:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_rag_command_exists(self, runner):
        result = runner.invoke(
            __import__("stock_robot.cli", fromlist=["main"]).main,
            ["rag", "--help"],
        )
        assert result.exit_code == 0
        assert "ingest" in result.output
        assert "clean" in result.output
        assert "stats" in result.output

    def test_rag_ingest_requires_source_type(self, runner, tmp_path):
        main_func = __import__("stock_robot.cli", fromlist=["main"]).main
        doc = tmp_path / "test.md"
        doc.write_text("# 测试", encoding="utf-8")
        result = runner.invoke(main_func, ["rag", "ingest", str(doc)])
        assert result.exit_code != 0

    def test_chat_command_help(self, runner):
        main_func = __import__("stock_robot.cli", fromlist=["main"]).main
        result = runner.invoke(main_func, ["chat", "--help"])
        assert result.exit_code == 0

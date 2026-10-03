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


@pytest.mark.parametrize("command", ["stats", "clean", "ingest"])
def test_rag_missing_chromadb_has_install_hint(monkeypatch, tmp_path, command):
    import builtins

    from rag.engine import RAGEngine
    from stock_robot.cli import main

    original_import = builtins.__import__

    def import_without_chromadb(name, *args, **kwargs):
        if name == "chromadb":
            raise ModuleNotFoundError("No module named 'chromadb'", name="chromadb")
        return original_import(name, *args, **kwargs)

    # 保留模块可导入，复现构造函数内部缺少可选包的真实失败点。
    assert RAGEngine is not None
    monkeypatch.setattr(builtins, "__import__", import_without_chromadb)
    args = ["rag", command]
    if command == "ingest":
        doc = tmp_path / "sample.md"
        doc.write_text("# 测试报告", encoding="utf-8")
        args += [str(doc), "--source-type", "research_reports"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    assert 'pip install -e ".[rag]"' in result.output


def test_rag_internal_import_error_is_not_optional_dependency_hint(monkeypatch):
    from rag.engine import RAGEngine
    from stock_robot.cli import _create_rag_engine

    def broken_init(self):
        raise ModuleNotFoundError("内部模块缺失", name="stock_robot_internal")

    monkeypatch.setattr(RAGEngine, "__init__", broken_init)
    with pytest.raises(ModuleNotFoundError) as caught:
        _create_rag_engine()
    assert caught.value.name == "stock_robot_internal"

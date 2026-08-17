from click.testing import CliRunner

from stock_robot.cli import main


class TestCLI:
    def test_analyze_without_symbol_shows_error(self):
        runner = CliRunner()
        result = runner.invoke(main, ["analyze"])
        assert result.exit_code != 0

    def test_analyze_with_invalid_symbol_shows_error(self, mocker):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        runner = CliRunner()
        result = runner.invoke(main, ["analyze", "abc"])
        assert result.exit_code != 0

    def test_analyze_with_valid_symbol(self, mocker):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        mocker.patch("utils.symbols.resolve_name", return_value="平安银行")
        mock_pipeline = mocker.patch("stock_robot.cli._build_pipeline")
        mock_instance = mock_pipeline.return_value
        from data.schemas import AnalysisContext, AnalysisResult
        ctx = AnalysisContext(symbol="000001", name="平安银行")
        mock_instance.run.return_value = (
            [AnalysisResult(dimension="financial", status="ok", summary="OK", metrics={"roe": 0.12})],
            {"bulk": "综合解读"},
            ctx,
        )

        runner = CliRunner()
        result = runner.invoke(main, ["analyze", "000001", "--no-llm"])
        assert result.exit_code == 0

    def test_config_set_and_get(self):
        runner = CliRunner()
        result = runner.invoke(main, ["config", "set", "llm.provider", "claude"])
        assert result.exit_code == 0
        result = runner.invoke(main, ["config", "get", "llm.provider"])
        assert result.exit_code == 0
        assert "claude" in result.output

    def test_cache_clear(self, mocker):
        mock_cache = mocker.patch("stock_robot.cli._get_cache")
        runner = CliRunner()
        result = runner.invoke(main, ["cache", "clear"])
        assert result.exit_code == 0
        mock_cache.return_value.clear.assert_called_once()

    def test_analyze_no_llm_flag(self, mocker):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        mocker.patch("utils.symbols.resolve_name", return_value="平安银行")
        mock_pipeline = mocker.patch("stock_robot.cli._build_pipeline")
        mock_instance = mock_pipeline.return_value
        from data.schemas import AnalysisContext, AnalysisResult
        ctx = AnalysisContext(symbol="000001", name="平安银行")
        mock_instance.run.return_value = (
            [AnalysisResult(dimension="financial", status="ok", summary="OK", metrics={})],
            {},
            ctx,
        )
        runner = CliRunner()
        result = runner.invoke(main, ["analyze", "000001", "--no-llm"])
        assert result.exit_code == 0


def test_api_command_help():
    """api 命令存在且可显示帮助"""
    from click.testing import CliRunner

    from stock_robot.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["api", "--help"])
    assert result.exit_code == 0
    assert "启动 Web API 服务" in result.output


def test_register_llm_keyless_config_registers_nothing(tmp_path):
    """无 api_key 的默认配置下 _register_llm 不应崩溃、不应注册后端"""
    from core.registry import Registry
    from stock_robot.cli import _register_llm
    from utils.config import Config

    config = Config(config_dir=tmp_path)
    reg = Registry()
    _register_llm(reg, config)  # 不应抛异常
    assert reg.get_llm_backend(config.get("llm.provider", "openai")) is None

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


def test_api_bind_resolution_uses_config_defaults(tmp_path):
    """未传 host/port 时读配置 api.host/api.port（默认 127.0.0.1:25618）"""
    from stock_robot.cli import _resolve_api_bind
    from utils.config import Config

    cfg = Config(config_dir=tmp_path)
    assert _resolve_api_bind(None, None, cfg) == ("127.0.0.1", 25618)
    assert _resolve_api_bind("0.0.0.0", None, cfg) == ("0.0.0.0", 25618)
    assert _resolve_api_bind(None, 9000, cfg) == ("127.0.0.1", 9000)


def test_api_bind_resolution_uses_configured_values(tmp_path):
    """配置自定义 api.port 后未传参时生效"""
    from stock_robot.cli import _resolve_api_bind
    from utils.config import Config

    cfg = Config(config_dir=tmp_path)
    cfg.set("api.port", 9000)
    cfg.set("api.host", "0.0.0.0")
    assert _resolve_api_bind(None, None, cfg) == ("0.0.0.0", 9000)
    assert _resolve_api_bind(None, 8000, cfg) == ("0.0.0.0", 8000)  # CLI 优先


def test_register_llm_keyless_config_registers_nothing(tmp_path):
    """无 api_key 的默认配置下 _register_llm 不应崩溃、不应注册后端"""
    from core.registry import Registry
    from stock_robot.cli import _register_llm
    from utils.config import Config

    config = Config(config_dir=tmp_path)
    reg = Registry()
    _register_llm(reg, config)  # 不应抛异常
    assert reg.get_llm_backend(config.get("llm.provider", "openai")) is None


class TestSubscribe:
    def test_add_creates_subscription(self, mocker, tmp_path):
        mock_store = mocker.patch("push.store.PushStore")
        instance = mock_store.return_value
        instance.create.return_value = 7
        runner = CliRunner()
        result = runner.invoke(main, [
            "subscribe", "add",
            "--name", "自选池",
            "--symbols", "600519,000300",
            "--channel", "email",
            "--time", "08:30",
        ])
        assert result.exit_code == 0
        created = instance.create.call_args.args[0]
        assert created.name == "自选池"
        assert [s.symbol for s in created.symbols] == ["600519", "000300"]
        assert created.channel == "email"

    def test_add_explicit_kind(self, mocker, tmp_path):
        mock_store = mocker.patch("push.store.PushStore")
        instance = mock_store.return_value
        instance.create.return_value = 8
        runner = CliRunner()
        result = runner.invoke(main, [
            "subscribe", "add",
            "--name", "指数池",
            "--symbols", "000001",
            "--kind", "index",
            "--index-style", "broad",
            "--channel", "email",
            "--time", "08:30",
        ])
        assert result.exit_code == 0
        created = instance.create.call_args.args[0]
        assert created.symbols[0].kind == "index"
        assert created.symbols[0].index_style == "broad"

    def test_add_invalid_channel(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "subscribe", "add",
            "--name", "t", "--symbols", "600519",
            "--channel", "sms", "--time", "08:30",
        ])
        assert result.exit_code != 0

    def test_list_prints_table(self, mocker, tmp_path):
        mock_store = mocker.patch("push.store.PushStore")
        from push.models import Subscription, SubscriptionSymbol
        mock_store.return_value.list.return_value = [
            Subscription(id=1, name="自选池",
                         symbols=[SubscriptionSymbol(symbol="600519")],
                         channel="email", time="08:30")]
        runner = CliRunner()
        result = runner.invoke(main, ["subscribe", "list"])
        assert result.exit_code == 0
        assert "自选池" in result.output

    def test_remove(self, mocker, tmp_path):
        mock_store = mocker.patch("push.store.PushStore")
        mock_store.return_value.delete.return_value = True
        runner = CliRunner()
        result = runner.invoke(main, ["subscribe", "remove", "--id", "3"])
        assert result.exit_code == 0
        mock_store.return_value.delete.assert_called_once_with(3)

    def test_run_triggers_executor(self, mocker, tmp_path):
        mock_store = mocker.patch("push.store.PushStore")
        mocker.patch("api.bootstrap.build_agent_core")  # 避免真实构建 AgentCore
        from push.models import Subscription, SubscriptionSymbol
        sub = Subscription(id=1, name="自选池",
                           symbols=[SubscriptionSymbol(symbol="600519")],
                           channel="email", time="08:30")
        mock_store.return_value.get.return_value = sub
        mock_executor = mocker.patch("push.executor.PushExecutor")
        mock_executor.return_value.run_subscription.return_value = {
            "total": 1, "ok": 1, "failures": []}
        runner = CliRunner()
        result = runner.invoke(main, ["subscribe", "run", "--id", "1"])
        assert result.exit_code == 0
        mock_executor.return_value.run_subscription.assert_called_once_with(sub)
        assert "1/1" in result.output


class TestIndustryMappingCommand:
    def test_update_symbol(self, mocker):
        from click.testing import CliRunner

        from stock_robot.cli import main

        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        mocker.patch("data.industry_mapping_builder.update_symbol",
                     return_value={"symbol": "600097", "sw_level1": "农林牧渔",
                                   "sw_level2": "渔业", "style_category": "必选消费",
                                   "action": "updated"})
        result = CliRunner().invoke(main, ["industry-mapping", "600097"])
        assert result.exit_code == 0
        assert "600097" in result.output
        assert "农林牧渔" in result.output

    def test_rebuild_all(self, mocker):
        from click.testing import CliRunner

        from stock_robot.cli import main

        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        mocker.patch("data.industry_mapping_builder.rebuild_all",
                     return_value={"total_industries": 335, "failed_industries": [],
                                   "stock_count": 5534, "coverage_pct": 98.2})
        result = CliRunner().invoke(main, ["industry-mapping"])
        assert result.exit_code == 0
        assert "5534" in result.output
        assert "98.2%" in result.output

    def test_invalid_symbol(self, mocker):
        from click.testing import CliRunner

        from stock_robot.cli import main

        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        result = CliRunner().invoke(main, ["industry-mapping", "abc"])
        assert result.exit_code == 1
        assert "无效" in result.output

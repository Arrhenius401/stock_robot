from datetime import date

import pandas as pd
from click.testing import CliRunner

from backtest.models import (
    BacktestRequest,
    BacktestResult,
    BacktestStrategy,
    BenchmarkSpec,
)
from stock_robot.cli import main


def test_create_cli_progress_uses_shared_transient_columns():
    from stock_robot.cli import _create_cli_progress

    progress = _create_cli_progress()

    assert progress.live.transient is True
    assert len(progress.columns) == 3
    assert all("task.completed" not in str(column) for column in progress.columns)


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

    def test_analyze_with_valid_symbol(self, mocker, tmp_path):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        mocker.patch("utils.symbols.resolve_name", return_value="平安银行")
        mock_pipeline = mocker.patch("stock_robot.cli._build_pipeline")
        mock_save = mocker.patch(
            "report.formatter.ReportFormatter.save",
            return_value=tmp_path / "stock" / "000001" / "2026-08" / "000001.md",
        )
        mock_instance = mock_pipeline.return_value
        from data.schemas import AnalysisContext, AnalysisResult
        ctx = AnalysisContext(symbol="000001", name="平安银行")
        def run_with_progress(*args, **kwargs):
            kwargs["on_progress"]("data", 1, 3, "读取")
            return (
                [AnalysisResult(dimension="financial", status="ok", summary="OK", metrics={"roe": 0.12})],
                {"bulk": "综合解读"},
                ctx,
            )

        mock_instance.run.side_effect = run_with_progress

        runner = CliRunner()
        result = runner.invoke(main, ["analyze", "000001", "--no-llm"])
        assert result.exit_code == 0
        assert mock_save.call_args.kwargs["category"] == "stock"

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


class TestBacktest:
    """回测 CLI 命令测试。"""

    @staticmethod
    def _fake_result() -> BacktestResult:
        """构造可完整通过产物写入的最小回测结果。"""
        idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="trade_date")
        return BacktestResult(
            equity_curve=pd.DataFrame(
                {
                    "策略净值": [1.0, 1.01],
                    "基准净值": [1.0, 1.0],
                    "目标仓位": [0.0, 0.7],
                    "当日信号": ["defend", "attack"],
                },
                index=idx,
            ),
            trades=pd.DataFrame(
                {
                    "signal_date": [date(2024, 1, 2)],
                    "trade_date": [date(2024, 1, 3)],
                    "reason": ["attack"],
                    "direction": ["buy"],
                    "price": [10.0],
                    "quantity": [7000],
                    "fees": [12.0],
                    "notional": [70000.0],
                }
            ),
            metrics={
                "累计收益": 0.01,
                "年化收益": 0.2,
                "最大回撤": -0.01,
                "年化波动率": 0.1,
                "夏普比率": 1.0,
                "交易次数": 1.0,
                "超额收益": 0.01,
            },
            warnings=[],
            request=BacktestRequest(
                symbol="000001",
                start_date=date(2024, 1, 2),
                end_date=date(2024, 12, 31),
                strategy_id="report_technical",
                benchmark_id="money_fund",
                initial_cash=100000.0,
            ),
            strategy=BacktestStrategy(
                id="report_technical",
                name="报告技术信号策略",
                version=1,
                signal_source="technical_score",
                thresholds={"attack": 7, "watch": 4},
                target_positions={"attack": 0.7, "watch": 0.4, "defend": 0.0},
                execution="next_open",
                warmup_days=90,
                cost_profile="a_share_default",
            ),
            benchmark=BenchmarkSpec(id="money_fund", name="中证货币型基金指数", symbol="H11025"),
            costs={
                "commission_rate": 0.0003,
                "minimum_commission": 5.0,
                "stamp_duty_rate": 0.0005,
                "transfer_fee_rate": 0.00001,
                "slippage_rate": 0.001,
            },
            data_start=date(2023, 9, 1),
            data_end=date(2024, 12, 31),
        )

    def test_backtest_cli_forwards_benchmark_override(self, mocker, tmp_path):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        fake_result = self._fake_result()
        mock_run = mocker.patch("backtest.runner.BacktestRunner.run", return_value=fake_result)
        mock_write = mocker.patch(
            "backtest.artifacts.write_backtest_artifacts",
            return_value=tmp_path / "reports" / "run_dir",
        )

        result = CliRunner().invoke(
            main,
            [
                "backtest", "000001",
                "--start", "2024-01-01",
                "--end", "2024-12-31",
                "--benchmark", "csi_300",
            ],
        )

        assert result.exit_code == 0
        assert mock_run.call_args.args[0].benchmark_id == "csi_300"
        mock_write.assert_called_once_with(fake_result)

    def test_backtest_cli_invalid_date_exits_without_output(self, mocker, tmp_path):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)

        result = CliRunner().invoke(
            main, ["backtest", "000001", "--start", "bad", "--end", "2024-12-31"]
        )

        assert result.exit_code != 0

    def test_backtest_cli_invalid_symbol_exits(self, mocker):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)

        result = CliRunner().invoke(
            main, ["backtest", "abc", "--start", "2024-01-01", "--end", "2024-12-31"]
        )

        assert result.exit_code != 0

    def test_backtest_cli_unaccepted_disclaimer_returns(self, mocker):
        mocker.patch("stock_robot.cli._check_disclaimer", return_value=False)
        mock_run = mocker.patch("backtest.runner.BacktestRunner.run")

        result = CliRunner().invoke(
            main, ["backtest", "000001", "--start", "2024-01-01", "--end", "2024-12-31"]
        )

        assert result.exit_code == 0
        mock_run.assert_not_called()


def test_run_command_help_is_available_and_api_is_unknown():
    """run 是唯一的 Web 服务启动命令。"""
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--help"])
    assert result.exit_code == 0
    assert "启动 Web API 服务" in result.output
    assert runner.invoke(main, ["api", "--help"]).exit_code != 0


def test_run_builds_server_and_reports_ready_url(mocker):
    """run 显示启动进度并将解析后的地址交给服务运行器。"""
    mock_core = mocker.patch("api.bootstrap.build_agent_core", return_value=object())
    mock_app = mocker.patch("api.app.create_app", return_value=object())
    mock_server = mocker.patch("stock_robot.cli._run_web_server")

    result = CliRunner().invoke(main, ["run", "--port", "8000"])

    assert result.exit_code == 0
    assert "正在启动 HTTP 服务" not in result.output
    assert "http://127.0.0.1:8000" in result.output
    mock_core.assert_called_once()
    mock_server.assert_called_once_with(mock_app.return_value, "127.0.0.1", 8000)


def test_run_web_server_does_not_write_console_status(mocker):
    """Uvicorn 日志已经覆盖运行状态，命令层不再输出 spinner。"""
    mocker.patch("uvicorn.run")
    status = mocker.patch("stock_robot.cli.console.status")

    from stock_robot.cli import _run_web_server
    _run_web_server(object(), "127.0.0.1", 8000)

    status.assert_not_called()


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
                                    "stock_count": 5534, "coverage_pct": 98.2,
                                    "valid_classification_rate": 99.1,
                                    "candidate_path": "candidate.csv"})
        result = CliRunner().invoke(main, ["industry-mapping"])
        assert result.exit_code == 0
        assert "5534" in result.output
        assert "98.2%" in result.output

    def test_rebuild_forwards_custom_delay_and_resume(self, mocker):
        from click.testing import CliRunner

        from stock_robot.cli import main

        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        rebuild = mocker.patch("data.industry_mapping_builder.rebuild_all", return_value={
            "total_industries": 335, "failed_industries": [], "stock_count": 5534,
            "coverage_pct": 98.2, "valid_classification_rate": 99.1,
            "candidate_path": "candidate.csv",
        })
        result = CliRunner().invoke(main, ["industry-mapping", "rebuild", "--resume", "--delay", "6"])
        assert result.exit_code == 0
        assert rebuild.call_args.kwargs["resume"] is True
        assert rebuild.call_args.kwargs["delay"] == 6.0

    def test_publish_candidate(self, mocker):
        from click.testing import CliRunner

        from stock_robot.cli import main

        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        mocker.patch("data.industry_mapping_builder.publish_candidate",
                     return_value={"stock_count": 5534, "valid_classification_rate": 99.1})
        result = CliRunner().invoke(main, ["industry-mapping", "publish"])
        assert result.exit_code == 0
        assert "候选映射已发布" in result.output

    def test_migrate_placeholders(self, mocker):
        from click.testing import CliRunner

        from stock_robot.cli import main

        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        migrate = mocker.patch("data.industry_mapping_builder.migrate_placeholder_rows",
                               return_value={"stock_count": 5534, "migrated_count": 5512})
        result = CliRunner().invoke(main, ["industry-mapping", "migrate-placeholders"])
        assert result.exit_code == 0
        assert "5512" in result.output
        migrate.assert_called_once()

    def test_validate_sample(self, mocker):
        from click.testing import CliRunner

        from stock_robot.cli import main

        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        mocker.patch("data.industry_mapping_builder.validate_sample",
                     return_value={"stock_count": 21, "valid_classification_rate": 100.0})
        result = CliRunner().invoke(main, ["industry-mapping", "validate"])
        assert result.exit_code == 0
        assert "抽样校验通过" in result.output

    def test_invalid_symbol(self, mocker):
        from click.testing import CliRunner

        from stock_robot.cli import main

        mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
        result = CliRunner().invoke(main, ["industry-mapping", "abc"])
        assert result.exit_code == 1
        assert "无效" in result.output

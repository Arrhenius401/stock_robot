"""黄金 CLI 与服务、归档共用结果；不请求真实网络。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from gold.models import GoldBar, GoldCollection, GoldSeries
from gold.service import GoldService, load_gold_snapshot
from stock_robot.cli import main


@pytest.fixture
def configured(mocker, tmp_path):
    config = SimpleNamespace(
        config_dir=tmp_path / "config", get=lambda key, default=None: default
    )
    mocker.patch("stock_robot.cli.Config", return_value=config)
    mocker.patch("stock_robot.cli._check_disclaimer", return_value=True)
    return config


def test_gold_help_has_refresh_no_llm_and_output_without_side_effects(mocker):
    config = mocker.patch("stock_robot.cli.Config")
    result = CliRunner().invoke(main, ["help", "gold"])
    assert result.exit_code == 0, result.output
    assert "--refresh" in result.output
    assert "--no-llm" in result.output
    assert "--output-dir" in result.output
    config.assert_not_called()


@pytest.mark.parametrize("refresh_flag", ["--refresh", "--refresh-cache"])
def test_gold_no_llm_reuses_service_and_passes_output_path(
    configured, mocker, tmp_path, refresh_flag
):
    build = mocker.patch("api.bootstrap.build_llm")
    service = mocker.patch("gold.service.GoldService")
    service.return_value.run.return_value = {
        "markdown": "# 黄金事实报告",
        "report_path": "gold/GOLD/report.md",
    }
    root = tmp_path / "custom-reports"
    result = CliRunner().invoke(
        main, ["gold", "--no-llm", refresh_flag, "--output-dir", str(root)]
    )
    assert result.exit_code == 0, result.output
    build.assert_not_called()
    service.assert_called_once_with(config=configured, llm=None, output_dir=root)
    service.return_value.run.assert_called_once_with(refresh_cache=True, no_llm=True)
    assert "黄金事实报告" in result.output
    assert "report.md" in result.output


def test_gold_builds_one_llm_but_no_agent_core(configured, mocker):
    llm = object()
    build = mocker.patch("api.bootstrap.build_llm", return_value=llm)
    agent = mocker.patch("api.bootstrap.build_agent_core")
    service = mocker.patch("gold.service.GoldService")
    service.return_value.run.return_value = {
        "markdown": "# 黄金事实报告",
        "report_path": "gold/GOLD/report.md",
    }
    result = CliRunner().invoke(main, ["gold", "--output", "markdown"])
    assert result.exit_code == 0, result.output
    build.assert_called_once_with(configured)
    agent.assert_not_called()
    assert service.call_args.kwargs["llm"] is llm
    service.return_value.run.assert_called_once_with(refresh_cache=False, no_llm=False)
    assert "黄金事实报告" not in result.output
    assert "report.md" in result.output


def test_gold_disabled_llm_configuration_skips_builder(configured, mocker):
    configured.get = lambda key, default=None: (
        False if key == "llm.enabled" else default
    )
    build = mocker.patch("api.bootstrap.build_llm")
    service = mocker.patch("gold.service.GoldService")
    service.return_value.run.return_value = {
        "markdown": "# 黄金事实报告",
        "report_path": "gold/GOLD/report.md",
    }
    result = CliRunner().invoke(main, ["gold"])
    assert result.exit_code == 0, result.output
    build.assert_not_called()
    service.return_value.run.assert_called_once_with(refresh_cache=False, no_llm=True)


def test_gold_service_error_is_actionable_and_points_to_help(configured, mocker):
    service = mocker.patch("gold.service.GoldService")
    service.return_value.run.side_effect = OSError("输出目录不可写")
    result = CliRunner().invoke(main, ["gold", "--no-llm"])
    assert result.exit_code == 1
    assert "输出目录不可写" in result.output
    assert "stock-robot help gold" in result.output


def test_gold_disclaimer_gate_avoids_collection(configured, mocker):
    mocker.patch("stock_robot.cli._check_disclaimer", return_value=False)
    service = mocker.patch("gold.service.GoldService")
    result = CliRunner().invoke(main, ["gold", "--no-llm"])
    assert result.exit_code == 0
    service.assert_not_called()


def test_gold_cli_real_service_analyzes_and_archives_same_facts(
    configured, mocker, tmp_path
):
    now = datetime(2026, 10, 26, tzinfo=UTC)
    bars = [
        GoldBar(
            trade_date=now.date() - timedelta(days=99 - i),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100 + i,
        )
        for i in range(100)
    ]
    prices = GoldSeries(
        key="domestic",
        name="国内黄金",
        source="离线样本",
        source_url="https://example.com",
        unit="人民币元/克",
        bars=bars,
        fetched_at=now,
    )
    collector = mocker.Mock()
    collector.collect.return_value = GoldCollection(
        generated_at=now, series={"domestic": prices}
    )
    results = []

    def create_service(**kwargs):
        service = GoldService(**kwargs, collector=collector)
        results.append(service)
        return service

    mocker.patch("gold.service.GoldService", side_effect=create_service)
    root = tmp_path / "reports"
    result = CliRunner().invoke(
        main, ["gold", "--no-llm", "--output-dir", str(root), "-o", "markdown"]
    )
    assert result.exit_code == 0, result.output
    files = list((root / "gold" / "GOLD").rglob("*.md"))
    assert len(files) == 1
    markdown = files[0].read_text(encoding="utf-8")
    snapshot = load_gold_snapshot(files[0], markdown)
    assert snapshot is not None
    assert snapshot["report_type"] == "gold"
    assert snapshot["markets"]["domestic"]["short_term"]["direction"] == "看多（强）"
    assert snapshot["long_term"]["direction"] == "无法判断"
    assert "黄金综合研报" in markdown
    assert len(results) == 1

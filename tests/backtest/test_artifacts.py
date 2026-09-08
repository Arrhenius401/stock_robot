"""回测产物写入测试 — 目录层级、五类文件与内容契约。"""

import json
from datetime import date, datetime

import pandas as pd
import pytest

from backtest.artifacts import write_backtest_artifacts
from backtest.models import (
    BacktestRequest,
    BacktestResult,
    BacktestStrategy,
    BenchmarkSpec,
)
from backtest.strategy import strategy_fingerprint


def _strategy() -> BacktestStrategy:
    return BacktestStrategy(
        id="report_technical",
        name="报告技术信号策略",
        version=1,
        signal_source="technical_score",
        thresholds={"attack": 7, "watch": 4},
        target_positions={"attack": 0.7, "watch": 0.4, "defend": 0.0},
        execution="next_open",
        warmup_days=90,
        cost_profile="a_share_default",
    )


def _build_result() -> BacktestResult:
    """构造可完整通过产物写入的最小回测结果。"""
    idx = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-04"], name="trade_date")
    equity = pd.DataFrame(
        {
            "策略净值": [1.0, 1.01, 1.02],
            "基准净值": [1.0, 1.001, 1.002],
            "目标仓位": [0.0, 0.7, 0.7],
            "当日信号": ["defend", "attack", "attack"],
        },
        index=idx,
    )
    trades = pd.DataFrame(
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
    )
    request = BacktestRequest(
        symbol="000001",
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 4),
        strategy_id="report_technical",
        benchmark_id="money_fund",
        initial_cash=100000.0,
    )
    return BacktestResult(
        equity_curve=equity,
        trades=trades,
        metrics={
            "累计收益": 0.02,
            "年化收益": 0.25,
            "最大回撤": -0.01,
            "年化波动率": 0.18,
            "夏普比率": 1.5,
            "交易次数": 1.0,
            "超额收益": 0.018,
        },
        warnings=["正式区间内无成交，指标仅反映空仓状态下的净值变化"],
        request=request,
        strategy=_strategy(),
        benchmark=BenchmarkSpec(id="money_fund", name="中证货币型基金指数", symbol="H11025"),
        costs={
            "commission_rate": 0.0003,
            "minimum_commission": 5.0,
            "stamp_duty_rate": 0.0005,
            "transfer_fee_rate": 0.00001,
            "slippage_rate": 0.001,
        },
        data_start=date(2023, 9, 1),
        data_end=date(2024, 1, 4),
    )


@pytest.fixture
def result() -> BacktestResult:
    return _build_result()


def test_artifact_path_contains_strategy_symbol_month_and_run_id(tmp_path, result):
    output = write_backtest_artifacts(result, tmp_path)

    assert output.relative_to(tmp_path).parts[:4] == (
        "backtests", "report_technical", "000001",
        datetime.now().astimezone().strftime("%Y-%m"),
    )
    for name in ("report.md", "summary.json", "equity_curve.csv", "trades.csv", "manifest.json"):
        assert (output / name).exists()


def test_manifest_snapshot_captures_run_context(tmp_path, result):
    output = write_backtest_artifacts(result, tmp_path)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["request"]["symbol"] == "000001"
    assert manifest["request"]["benchmark_id"] == "money_fund"
    assert manifest["request"]["start_date"] == "2024-01-02"
    assert manifest["data_start"] == "2023-09-01"
    assert manifest["data_end"] == "2024-01-04"
    assert manifest["strategy"]["id"] == "report_technical"
    assert manifest["strategy"]["version"] == 1
    assert manifest["fingerprint"] == strategy_fingerprint(result.strategy)
    assert manifest["benchmark"] == {
        "id": "money_fund",
        "name": "中证货币型基金指数",
        "symbol": "H11025",
    }
    assert manifest["costs"]["slippage_rate"] == pytest.approx(0.001)
    assert manifest["warnings"] == result.warnings
    assert manifest["version"]


def test_summary_is_flat_and_stable(tmp_path, result):
    output = write_backtest_artifacts(result, tmp_path)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))

    assert summary["symbol"] == "000001"
    assert summary["strategy_id"] == "report_technical"
    assert summary["strategy_version"] == 1
    assert summary["run_id"] == output.name
    assert summary["start_date"] == "2024-01-02"
    assert summary["end_date"] == "2024-01-04"
    for key, value in result.metrics.items():
        assert summary[key] == pytest.approx(value)
    assert summary["warnings_count"] == 1


def test_equity_curve_csv_dates_are_iso(tmp_path, result):
    output = write_backtest_artifacts(result, tmp_path)
    frame = pd.read_csv(output / "equity_curve.csv", encoding="utf-8")

    assert list(frame.columns) == ["净值日期", "策略净值", "基准净值", "目标仓位", "当日信号"]
    assert frame["净值日期"].iloc[0] == "2024-01-02"
    assert frame["当日信号"].iloc[1] == "attack"


def test_trades_csv_dates_are_iso(tmp_path, result):
    output = write_backtest_artifacts(result, tmp_path)
    frame = pd.read_csv(output / "trades.csv", encoding="utf-8")

    assert frame["signal_date"].iloc[0] == "2024-01-02"
    assert frame["trade_date"].iloc[0] == "2024-01-03"


def test_report_md_contains_required_sections(tmp_path, result):
    output = write_backtest_artifacts(result, tmp_path)
    md = (output / "report.md").read_text(encoding="utf-8")

    assert "历史表现不代表未来收益" in md
    assert "策略年化" in md
    assert "报告技术信号策略" in md
    assert "中证货币型基金指数" in md
    assert "100000.00" in md  # 初始资金
    assert "累计收益" in md
    assert "attack" in md  # 最近交易表


def test_write_failure_cleans_up_tmp_and_leaves_no_run_dir(tmp_path, result, monkeypatch):
    def _boom(self, *args, **kwargs):
        raise OSError("模拟写入失败")

    monkeypatch.setattr(pd.DataFrame, "to_csv", _boom)

    with pytest.raises(OSError, match="模拟写入失败"):
        write_backtest_artifacts(result, tmp_path)

    # 失败后临时目录被清理，最终运行目录从未创建
    month = datetime.now().astimezone().strftime("%Y-%m")
    month_dir = tmp_path / "backtests" / "report_technical" / "000001" / month
    assert list(month_dir.iterdir()) == []

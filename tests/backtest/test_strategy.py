from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from pydantic import ValidationError

from backtest.models import BacktestStrategy
from backtest.strategy import (
    StrategyConfigError,
    StrategyRepository,
    strategy_fingerprint,
)


def _strategy_yaml() -> str:
    return (
        "id: report_technical\n"
        "name: 报告技术信号策略\n"
        "version: 1\n"
        "signal_source: technical_score\n"
        "thresholds:\n"
        "  attack: 7\n"
        "  watch: 4\n"
        "target_positions:\n"
        "  attack: 0.7\n"
        "  watch: 0.4\n"
        "  defend: 0.0\n"
        "execution: next_open\n"
        "warmup_days: 90\n"
        "cost_profile: a_share_default\n"
    )


def test_backtest_strategy_rejects_invalid_id_and_warmup():
    with pytest.raises(ValidationError, match="id"):
        BacktestStrategy.model_validate(
            {
                "id": "Report-Technical",
                "name": "报告技术信号策略",
                "version": 1,
                "signal_source": "technical_score",
                "thresholds": {"attack": 7, "watch": 4},
                "target_positions": {"attack": 0.7, "watch": 0.4, "defend": 0.0},
                "execution": "next_open",
                "warmup_days": 90,
                "cost_profile": "a_share_default",
            }
        )

    with pytest.raises(ValidationError, match="warmup_days"):
        BacktestStrategy.model_validate(
            {
                "id": "report_technical",
                "name": "报告技术信号策略",
                "version": 1,
                "signal_source": "technical_score",
                "thresholds": {"attack": 7, "watch": 4},
                "target_positions": {"attack": 0.7, "watch": 0.4, "defend": 0.0},
                "execution": "next_open",
                "warmup_days": 30,
                "cost_profile": "a_share_default",
            }
        )


def test_backtest_strategy_rejects_invalid_position_order():
    with pytest.raises(ValidationError, match="target_positions"):
        BacktestStrategy.model_validate(
            {
                "id": "report_technical",
                "name": "报告技术信号策略",
                "version": 1,
                "signal_source": "technical_score",
                "thresholds": {"attack": 7, "watch": 4},
                "target_positions": {"attack": 0.4, "watch": 0.7, "defend": 0.0},
                "execution": "next_open",
                "warmup_days": 90,
                "cost_profile": "a_share_default",
            }
        )


def test_repository_loads_default_strategy_and_fingerprint_is_stable():
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        strategies_dir = Path(tmp_dir) / "strategies"
        strategies_dir.mkdir()
        (strategies_dir / "report_technical.yaml").write_text(_strategy_yaml(), encoding="utf-8")

        repo = StrategyRepository(strategies_dir)
        strategy = repo.get("report_technical")
        again = repo.get("report_technical")

        assert strategy.id == "report_technical"
        assert strategy.signal_source == "technical_score"
        assert strategy.execution == "next_open"
        assert strategy_fingerprint(strategy) == strategy_fingerprint(again)

        changed = strategy.model_copy(update={"target_positions": {"attack": 0.8, "watch": 0.4, "defend": 0.0}})
        assert strategy_fingerprint(changed) != strategy_fingerprint(strategy)


def test_repository_rejects_duplicate_strategy_id():
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        strategies_dir = Path(tmp_dir) / "strategies"
        strategies_dir.mkdir()
        (strategies_dir / "a.yaml").write_text(_strategy_yaml(), encoding="utf-8")
        (strategies_dir / "b.yaml").write_text(_strategy_yaml(), encoding="utf-8")

        with pytest.raises(StrategyConfigError, match="重复"):
            StrategyRepository(strategies_dir).load_all()

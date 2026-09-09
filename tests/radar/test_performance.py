"""单标的历史表现计算测试。"""

from datetime import date, timedelta

import pandas as pd
import pytest

from radar.performance import calculate_instrument_performance


def test_instrument_performance_uses_its_own_curve_and_three_benchmarks():
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(4)]
    history = pd.DataFrame({"date": dates, "close": [100.0, 110.0, 99.0, 121.0]})
    benchmarks = {
        "money_fund": pd.Series([100.0, 101.0, 102.0, 103.0], index=dates),
        "csi_300": pd.Series([100.0, 102.0, 98.0, 105.0], index=dates),
        "csi_all_bond": pd.Series([100.0, 100.5, 101.0, 101.5], index=dates),
    }

    result = calculate_instrument_performance(history, benchmarks)

    assert result.metrics["累计收益"] == pytest.approx(0.21)
    assert result.metrics["最大回撤"] == pytest.approx(-0.1)
    assert result.benchmark_metrics["csi_300"]["累计收益"] == pytest.approx(0.05)
    assert {"instrument_equity", "csi_300_equity", "csi_300_relative_equity"} <= set(result.equity_curve.columns)

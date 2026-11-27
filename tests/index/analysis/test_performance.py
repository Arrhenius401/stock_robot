"""历史窗口与独立ETF表现契约。"""
from datetime import date, timedelta

import pytest

from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexPriceData
from index.analysis.performance import IndexPerformanceAnalyzer


def test_performance_includes_three_and_five_year_windows():
    prices = [IndexPriceData(symbol="000300", trade_date=date(2021, 1, 1) + timedelta(days=30*i),
             open=100+i, high=101+i, low=99+i, close=100+i, volume=1) for i in range(61)]
    target = AnalysisTarget(target_type="index", symbol="000300", name="沪深300", market="a-shares", index_style="broad")
    result = IndexPerformanceAnalyzer().analyze(IndexAnalysisContext(target=target, price_data=[*prices]))
    windows = {w["period"]: w for w in result.metrics["windows"]}
    assert {"36m", "60m"} <= windows.keys()
    assert windows["60m"]["return_pct"] == pytest.approx(60)
    assert "历史不足完整窗口" in windows["60m"]["notes"]

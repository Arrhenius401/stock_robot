from datetime import date

import pytest

from radar.backtest_cache import derive_backtest_window


def test_derives_short_window_from_completed_longer_backtest():
    payload = {
        "summary": {"strategy_id": "core_rotation_v1"},
        "equity_curve": {"rows": [
            {"date": "2025-01-02", "equity": "100", "strategy_equity": "1", "csi_300_equity": "1"},
            {"date": "2025-02-03", "equity": "110", "strategy_equity": "1.1", "csi_300_equity": "1.05"},
            {"date": "2025-03-03", "equity": "121", "strategy_equity": "1.21", "csi_300_equity": "1.1"},
        ]},
        "trades": {"rows": [
            {"trade_date": "2025-01-02", "symbol": "510500", "gross": "100"},
            {"trade_date": "2025-02-03", "symbol": "510500", "gross": "55"},
        ]},
    }

    result = derive_backtest_window(payload, date(2025, 2, 1))

    assert result["cache"] == {
        "status": "derived",
        "requested_start_date": "2025-02-01",
        "actual_start_date": "2025-02-03",
        "actual_end_date": "2025-03-03",
    }
    assert result["summary"]["累计收益"] == pytest.approx(0.1)
    assert result["summary"]["交易次数"] == 1.0
    assert result["summary"]["benchmarks"]["csi_300"]["超额累计收益"] == pytest.approx(0.05238, abs=0.00001)
    assert result["equity_curve"]["rows"][0]["strategy_equity"] == 1.0

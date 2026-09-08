"""雷达回测产物的原子目录测试。"""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from radar.artifacts import write_radar_backtest_artifacts
from radar.backtest import RadarBacktestResult


def test_writes_auditable_radar_backtest_artifacts():
    result = RadarBacktestResult(
        pd.DataFrame({"equity": [100.0]}, index=[date(2024, 1, 2)]),
        pd.DataFrame(), {"累计收益": 0.0}, (date(2024, 1, 1),),
    )
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        output = write_radar_backtest_artifacts(
            result, universe_id="cn_hk_etf", universe_version=1,
            strategy_id="core_rotation_v1", strategy_fingerprint="abc123",
            start_date=date(2024, 1, 1), end_date=date(2024, 1, 2), reports_dir=Path(tmp_dir),
        )
        assert (output / "manifest.json").exists()
        assert '"strategy_fingerprint": "abc123"' in (output / "manifest.json").read_text(encoding="utf-8")

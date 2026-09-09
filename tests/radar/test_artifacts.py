"""雷达回测产物的原子目录测试。"""

import json
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


def test_artifacts_persist_benchmark_configuration_and_metrics():
    result = RadarBacktestResult(
        pd.DataFrame({"equity": [100.0], "strategy_equity": [1.0], "csi_300_equity": [1.0]}, index=[date(2024, 1, 2)]),
        pd.DataFrame(), {"累计收益": 0.0}, (),
        {"csi_300": {"超额累计收益": 0.0}},
    )
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        output = write_radar_backtest_artifacts(
            result, universe_id="cn_hk_etf", universe_version=1,
            strategy_id="core_rotation_v1", start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2), benchmarks=[{"id": "csi_300", "symbol": "000300", "name": "沪深300"}], reports_dir=Path(tmp_dir),
        )
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert summary["benchmarks"]["csi_300"]["超额累计收益"] == 0.0
        assert manifest["benchmarks"][0]["id"] == "csi_300"


def test_artifacts_persist_replay_configuration_and_warnings():
    result = RadarBacktestResult(
        pd.DataFrame({"equity": [100.0]}, index=[date(2024, 1, 2)]),
        pd.DataFrame(), {"累计收益": 0.0, "交易次数": 0.0, "换手率": 0.0}, (),
        warnings=("2024-01-02 510300 缺少开盘价，保留原持仓",),
    )
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        output = write_radar_backtest_artifacts(
            result, universe_id="cn_hk_etf", universe_version=1,
            strategy_id="core_rotation_v1", start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            strategy_snapshot={"id": "core_rotation_v1", "execution": "next_open"},
            cost_profile={"id": "etf_default", "commission_rate": 0.0003, "slippage_rate": 0.0002},
            data_coverage={"formal_start": "2024-01-01", "formal_end": "2024-01-02"},
            data_providers={"etf": ["AkShareETFDataProvider"]}, reports_dir=Path(tmp_dir),
        )
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

        assert summary["warnings"] == ["2024-01-02 510300 缺少开盘价，保留原持仓"]
        assert manifest["strategy"]["execution"] == "next_open"
        assert manifest["cost_profile"]["slippage_rate"] == 0.0002
        assert manifest["data_coverage"]["formal_end"] == "2024-01-02"
        assert manifest["data_providers"]["etf"] == ["AkShareETFDataProvider"]

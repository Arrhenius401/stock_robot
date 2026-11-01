"""雷达回测产物的原子目录测试。"""

import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import pytest

from radar.artifacts import _replace_with_retry, write_radar_backtest_artifacts
from radar.backtest import RadarBacktestResult


@pytest.mark.parametrize("failures", [0, 1, 2])
def test_atomic_replace_retries_only_transient_permission_errors(tmp_path, monkeypatch, failures):
    source, target = tmp_path / "run.tmp", tmp_path / "run"
    calls = []
    waits = []

    def replace(source_path, target_path):
        calls.append((source_path, target_path))
        if len(calls) <= failures:
            raise PermissionError("模拟文件扫描短暂占用")

    monkeypatch.setattr("radar.artifacts.os.replace", replace)
    monkeypatch.setattr("radar.artifacts.time.sleep", waits.append)
    _replace_with_retry(source, target)
    assert calls == [(source, target)] * (failures + 1)
    assert waits == [0.05] * failures


def test_atomic_replace_permission_failure_is_bounded_and_preserves_exception(tmp_path, monkeypatch):
    error = PermissionError("模拟持续权限错误")
    calls = []
    waits = []

    def replace(source, target):
        calls.append((source, target))
        raise error

    monkeypatch.setattr("radar.artifacts.os.replace", replace)
    monkeypatch.setattr("radar.artifacts.time.sleep", waits.append)
    with pytest.raises(PermissionError) as raised:
        _replace_with_retry(tmp_path / "run.tmp", tmp_path / "run")
    assert raised.value is error
    assert len(calls) == 3
    assert waits == [0.05, 0.05]


@pytest.mark.parametrize("error", [FileNotFoundError("临时目录丢失"), FileExistsError("目标冲突"), OSError("其他 I/O 错误")])
def test_atomic_replace_does_not_retry_unrelated_errors(tmp_path, monkeypatch, error):
    calls = []
    waits = []

    def replace(source, target):
        calls.append((source, target))
        raise error

    monkeypatch.setattr("radar.artifacts.os.replace", replace)
    monkeypatch.setattr("radar.artifacts.time.sleep", waits.append)
    with pytest.raises(type(error)) as raised:
        _replace_with_retry(tmp_path / "run.tmp", tmp_path / "run")
    assert raised.value is error
    assert len(calls) == 1
    assert waits == []


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

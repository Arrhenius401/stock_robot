"""配置雷达固定基准目录测试。"""

from datetime import date
from pathlib import Path

import pandas as pd

from radar.benchmarks import (
    LocalBenchmarkStore,
    RadarBenchmark,
    fetch_benchmark_closes,
    load_benchmarks,
)


def test_radar_benchmark_catalog_contains_three_fixed_indices():
    root = Path(__file__).parents[2]

    catalog = load_benchmarks(root / "config" / "radar_benchmarks.yaml")

    assert {item.id for item in catalog.benchmarks} == {"money_fund", "csi_300", "csi_all_bond"}


def test_local_benchmark_is_used_when_network_is_unavailable(tmp_path, monkeypatch):
    source = tmp_path / "csi300.csv"
    pd.DataFrame({"日期": ["2024-01-02", "2024-01-03"], "收盘": [3500.0, 3510.0]}).to_csv(source, index=False, encoding="utf-8-sig")
    directory = tmp_path / "local"
    LocalBenchmarkStore(directory).import_csv("csi_300", source)
    monkeypatch.setattr("radar.benchmarks._ak_csindex", lambda **_: (_ for _ in ()).throw(ConnectionError("offline")))
    benchmark = RadarBenchmark(id="csi_300", name="沪深300", symbol="000300")

    result = fetch_benchmark_closes(
        benchmark,
        date(2024, 1, 1),
        date(2024, 1, 31),
        local_directory=directory,
    )

    assert result.tolist() == [3500.0, 3510.0]

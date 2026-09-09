"""配置雷达固定基准目录测试。"""

from pathlib import Path

from radar.benchmarks import load_benchmarks


def test_radar_benchmark_catalog_contains_three_fixed_indices():
    root = Path(__file__).parents[2]

    catalog = load_benchmarks(root / "config" / "radar_benchmarks.yaml")

    assert {item.id for item in catalog.benchmarks} == {"money_fund", "csi_300", "csi_all_bond"}

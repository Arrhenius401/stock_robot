"""配置雷达固定窗口稳健性报告测试。"""

import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from radar.walkforward import (
    fixed_windows,
    summarize_windows,
    write_walkforward_artifacts,
)


def _completed_artifact(root: Path, name: str, excess: float) -> Path:
    output = root / name
    output.mkdir()
    (output / "summary.json").write_text(json.dumps({
        "累计收益": 0.1,
        "年化收益": 0.1,
        "最大回撤": -0.08,
        "夏普比率": 1.1,
        "交易次数": 4.0,
        "benchmarks": {"csi_300": {"超额累计收益": excess}},
    }), encoding="utf-8")
    return output


def test_fixed_windows_are_continuous_and_preserve_incomplete_tail():
    windows = fixed_windows(date(2020, 1, 1), date(2025, 6, 30), 2)

    assert [(item.start_date, item.end_date, item.available) for item in windows] == [
        (date(2020, 1, 1), date(2021, 12, 31), True),
        (date(2022, 1, 1), date(2023, 12, 31), True),
        (date(2024, 1, 1), date(2025, 6, 30), False),
    ]


def test_summary_keeps_positive_negative_and_unavailable_windows():
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        root = Path(tmp_dir)
        windows = fixed_windows(date(2020, 1, 1), date(2025, 6, 30), 2)
        outputs = {
            (date(2020, 1, 1), date(2021, 12, 31)): _completed_artifact(root, "positive", 0.12),
            (date(2022, 1, 1), date(2023, 12, 31)): _completed_artifact(root, "negative", -0.04),
        }

        summary = summarize_windows(windows, outputs, {})

        assert [record["status"] for record in summary["windows"]] == ["completed", "completed", "unavailable"]
        assert summary["positive_excess"]["csi_300"]["positive_excess_ratio"] == 0.5
        assert summary["unavailable_windows"] == 1


def test_write_walkforward_artifacts_writes_summary_and_manifest():
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        output = write_walkforward_artifacts(
            {"windows": [], "completed_windows": 0, "unavailable_windows": 0, "positive_excess": {}},
            universe_id="cn_hk_etf", strategy_id="core_rotation_v1",
            start_date=date(2020, 1, 1), end_date=date(2025, 12, 31),
            window_years=2, reports_dir=Path(tmp_dir),
        )

        assert (output / "summary.json").is_file()
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["window_years"] == 2
        assert "## 分段策略结果" in (output / "report.md").read_text(encoding="utf-8")

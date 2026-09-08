"""雷达回测报告库发现测试。"""

from pathlib import Path
from tempfile import TemporaryDirectory

from api.report_library import get_report_detail, list_reports


def test_report_library_discovers_complete_radar_backtest():
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        run_dir = Path(tmp_dir) / "radar_backtests" / "etf" / "core_rotation_v1" / "cn_hk_etf" / "2026-09" / "run"
        run_dir.mkdir(parents=True)
        (run_dir / "report.md").write_text("# 回测\n", encoding="utf-8")
        (run_dir / "summary.json").write_text("{}", encoding="utf-8")
        (run_dir / "manifest.json").write_text('{"universe_id":"cn_hk_etf","strategy_id":"core_rotation_v1"}', encoding="utf-8")
        reports = list_reports(Path(tmp_dir), report_type="backtest")

        assert len(reports) == 1
        assert reports[0].symbol == "cn_hk_etf"
        assert get_report_detail(Path(tmp_dir), reports[0].id).report.id == reports[0].id

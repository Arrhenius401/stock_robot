"""报告库服务测试：枚举、读取与路径安全。"""
from pathlib import Path

import pytest

from api.report_library import (
    ReportLibraryError,
    get_report_detail,
    list_reports,
    resolve_download_path,
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_reports(root: Path) -> None:
    write(root / "stock" / "000001" / "2026-08" / "000001_20260829.md",
          "# 平安银行\n\n个股正文")
    write(root / "index" / "000300" / "2026-08" / "000300_20260829.md",
          "# 沪深300\n\n指数正文")
    run = root / "backtests" / "report_technical" / "000001" / "2026-08" / "run-1"
    write(run / "report.md", "# 回测报告\n\n策略正文")
    write(run / "summary.json",
          '{"symbol":"000001","strategy_id":"report_technical","strategy_version":"v1",'
          '"run_id":"run-1","start_date":"2025-01-02","end_date":"2026-08-28",'
          '"metrics":{"total_return":0.1842,"max_drawdown":-0.0786,'
          '"sharpe":1.21},"trades_count":26}')
    write(run / "manifest.json", '{"strategy":{"name":"技术策略"}}')
    write(run / "equity_curve.csv",
          "净值日期,策略净值,基准净值\n2026-01-01,1.0,1.0\n2026-01-02,1.1,1.02\n")
    write(run / "trades.csv",
          "trade_date,side,price,return_pct\n2026-03-18,buy,10.42,\n"
          "2026-04-26,sell,11.31,0.0854\n")
    write(root / "600519" / "2026-08" / "600519_20260829.md",
          "# 贵州茅台\n\n旧版个股正文")


def test_list_reports_reads_all_supported_products(tmp_path):
    reports = tmp_path / "reports"
    make_reports(reports)

    summaries = list_reports(reports)

    assert [item.type for item in summaries] == ["stock", "index", "backtest", "stock"]
    assert any(item.title == "000001 技术策略回测报告" and item.has_equity_curve
               and item.has_trades for item in summaries)
    assert any(item.title == "600519 个股分析报告" and item.legacy for item in summaries)


def test_list_reports_filters_type_and_query(tmp_path):
    reports = tmp_path / "reports"
    make_reports(reports)

    assert [item.type for item in list_reports(reports, report_type="index")] == ["index"]
    assert [item.symbol for item in list_reports(reports, query="茅台")] == ["600519"]


def test_detail_reads_backtest_payload_and_missing_csv_is_nonfatal(tmp_path):
    reports = tmp_path / "reports"
    make_reports(reports)
    summary = next(item for item in list_reports(reports) if item.type == "backtest")
    (reports / "backtests" / "report_technical" / "000001" / "2026-08"
     / "run-1" / "trades.csv").unlink()

    detail = get_report_detail(reports, summary.id)

    assert detail.markdown.startswith("# 回测报告")
    assert detail.summary["symbol"] == "000001"
    assert detail.equity_curve["columns"] == ["净值日期", "策略净值", "基准净值"]
    assert detail.trades["rows"] == []
    assert detail.missing_artifacts == ["trades.csv"]


def test_rejects_unknown_id_and_path_escape(tmp_path):
    reports = tmp_path / "reports"
    make_reports(reports)

    with pytest.raises(ReportLibraryError) as unknown:
        get_report_detail(reports, "not-a-valid-report-id")
    assert unknown.value.status_code == 404

    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    escaped_id = "Li4vb3V0c2lkZS5tZA"
    with pytest.raises(ReportLibraryError) as escaped:
        resolve_download_path(reports, escaped_id)
    assert escaped.value.status_code == 404

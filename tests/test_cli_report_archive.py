"""命令行生成的个股报告与报告库共用存档快照。"""
from types import SimpleNamespace

from click.testing import CliRunner

from api.report_library import get_report_detail, list_reports
from data.schemas import AnalysisContext, AnalysisResult
from stock_robot.cli import main


def test_cli_stock_report_archives_same_analysis(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("stock_robot.cli._check_disclaimer", lambda _: True)
    monkeypatch.setattr("utils.symbols.resolve_name", lambda _: "样本银行")
    ctx = AnalysisContext(symbol="000001", name="样本银行")
    result = AnalysisResult(dimension="technical", status="unavailable", summary="行情不足")
    pipeline = SimpleNamespace(run=lambda *_, **__: ([result], {}, ctx))
    monkeypatch.setattr("stock_robot.cli._build_pipeline", lambda **_: pipeline)
    invocation = CliRunner().invoke(main, ["analyze", "000001", "--no-llm"])
    assert invocation.exit_code == 0, invocation.output
    root = tmp_path / "reports"
    detail = get_report_detail(root, list_reports(root)[0].id)
    assert detail.stock_payload is not None
    assert detail.stock_payload["name"] == "样本银行"
    assert detail.stock_payload["score"]["final"] is None
    assert detail.stock_payload["commentary_status"] == "insufficient"
    assert "行情不足" in detail.markdown

"""Phase 2 单值采集结果契约。"""
from datetime import date
from types import SimpleNamespace

import pandas as pd

from data.akshare import get_total_shares, get_total_shares_result
from utils.symbols import resolve_name, resolve_name_result


def test_shares_sources_and_compatibility(monkeypatch):
    monkeypatch.setattr("data.akshare._ak_individual_info_em", lambda symbol: pd.DataFrame({"item": ["总股本"], "value": ["123"]}))
    result = get_total_shares_result("600519")
    assert result.value == 123
    assert result.record.source == "东财总股本"
    assert result.record.result_state == "complete"
    assert result.record.collected_at is not None
    assert get_total_shares("600519") == 123

    monkeypatch.setattr("data.akshare._ak_individual_info_em", lambda symbol: None)
    monkeypatch.setattr("data.akshare._ak_daily", lambda **kwargs: pd.DataFrame({"outstanding_share": [456]}))
    assert get_total_shares_result("600519").record.source == "新浪流通股本"

    monkeypatch.setattr("data.akshare._ak_daily", lambda **kwargs: None)
    fin = SimpleNamespace(fiscal_quarter=date(2025, 12, 31), net_profit=100, basic_eps=2)
    result = get_total_shares_result("600519", [fin])
    assert result.value == 50
    assert result.record.source == "财报反推"
    result = get_total_shares_result("600519")
    assert result.value is None
    assert result.record.result_state == "failed"
    assert get_total_shares("600519") is None


def test_name_result_sources_and_failure(monkeypatch):
    monkeypatch.setattr("utils.symbols._ak_code_name", lambda: pd.DataFrame({"code": ["600519"], "name": ["贵州茅台"]}))
    result = resolve_name_result("600519")
    assert result.value == "贵州茅台"
    assert result.record.source == "AkShare A股代码名称"
    assert result.record.result_state == "complete"
    assert resolve_name("600519") == "贵州茅台"
    monkeypatch.setattr("data.hk_eastmoney.stock_snapshot", lambda symbol: {"f58": "腾讯控股"})
    assert resolve_name_result("00700").record.source == "东财港股快照"
    monkeypatch.setattr("data.hk_eastmoney.stock_snapshot", lambda symbol: {})
    result = resolve_name_result("00700")
    assert result.value is None
    assert result.record.result_state == "failed"
    assert resolve_name("00700") == ""


def test_name_exception_has_safe_diagnostic(monkeypatch):
    def fail():
        raise RuntimeError("敏感的上游细节")

    monkeypatch.setattr("utils.symbols._ak_code_name", fail)
    result = resolve_name_result("600519")
    assert result.value is None
    assert result.record.result_state == "failed"
    assert result.record.error_kind == "fetch_failed"
    assert "敏感的上游细节" not in result.record.error_message

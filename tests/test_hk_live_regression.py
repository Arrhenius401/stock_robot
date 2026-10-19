"""腾讯 QQ 失败报告的跨层回归：行情故障不应抹掉真实财报和估值。"""

import json
from pathlib import Path

import pytest
import requests

from analysis.financial import FinancialAnalyzer
from analysis.industry import IndustryAnalyzer
from analysis.sentiment import SentimentAnalyzer
from analysis.technical import TechnicalAnalyzer
from analysis.valuation import ValuationAnalyzer
from core.pipeline import Pipeline
from core.registry import Registry
from data.akshare import AkShareAdapter
from report.formatter import ReportFormatter
from report.payload import build_stock_payload
from report.scoring import build_report
from utils.config import Config
from utils.symbols import normalize_symbol, resolve_name, symbol_market

RESOURCES = Path(__file__).parent / "resources"


@pytest.mark.parametrize("cash_flow_failure", [False, True])
def test_real_financial_rows_survive_price_outage_through_saved_report(tmp_path, monkeypatch, cash_flow_failure):
    monkeypatch.chdir(tmp_path)
    snapshot = json.loads((RESOURCES / "data/eastmoney-00700-valuation-snapshot.json").read_text(encoding="utf-8"))

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self.data

    def get(url, **kwargs):
        if "/securities/api/data/" in url:
            statements = {"RPT_HKF10_FN_INCOME_PC": "income", "RPT_HKF10_FN_BALANCE_PC": "balance", "RPT_HKF10_FN_CASHFLOW_PC": "cash-flow"}
            kind = statements[kwargs["params"]["reportName"]]
            if cash_flow_failure and kind == "cash-flow":
                raise requests.Timeout("复现云端现金流量表超时")
            path = RESOURCES / "data" / f"eastmoney-00700-{kind}-report.json"
            rows = json.loads(path.read_text(encoding="utf-8"))
            return Response({"success": True, "result": {"data": rows, "count": len(rows), "pages": 1}})
        assert kwargs["params"]["secid"] == "116.00700"
        if "/stock/get" in url:
            return Response(snapshot)
        raise requests.ConnectionError("复现云端行情连接被上游断开")

    monkeypatch.setattr("data.hk_eastmoney.requests.get", get)
    registry = Registry()
    registry.register_data_source(AkShareAdapter())
    for analyzer in (FinancialAnalyzer(), TechnicalAnalyzer(), ValuationAnalyzer(), IndustryAnalyzer(), SentimentAnalyzer()):
        registry.register_analysis_module(analyzer)
    config = Config(tmp_path / ".stock_robot")
    config.set("llm.enabled", False)
    symbol = normalize_symbol("hk00700")
    name = resolve_name(symbol)
    assert name == "腾讯控股"
    pipeline = Pipeline(registry, config=config, llm_enabled=False)
    results, commentary, context = pipeline.run(symbol, name, market=symbol_market(symbol))
    assert context.market == "hk"
    assert context.price_data is None
    assert context.financial_data is not None and len(context.financial_data) == 5
    latest = max(context.financial_data, key=lambda item: item.fiscal_quarter)
    assert latest.revenue == 396431000000
    assert latest.net_profit == 114115000000
    assert latest.total_assets == 2159394000000
    assert latest.operating_cash_flow == (None if cash_flow_failure else 154061000000)
    if cash_flow_failure:
        assert context.collection_records["financial"].error_kind == "timeout"
        assert "现金流量表" in context.collection_records["financial"].error_message
    assert context.valuation_data is not None
    assert context.valuation_data.pe_ttm == 14.23
    assert context.valuation_data.pb == 2.9
    assert context.enriched_valuation is not None
    assert context.enriched_valuation.daily_points == []
    payload = build_stock_payload(symbol, name, results, commentary, context, no_llm=True)
    assert payload["market"] == "hk"
    assert payload["data_quality"]["has_analysis_data"] is True
    assert payload["data_quality"]["has_scored_data"] is False
    assert payload["commentary_status"] == "disabled"
    assert payload["score"]["final"] is None
    assert payload["signal"] is None
    original = (RESOURCES / "report/qq-00700-unavailable-report.md").read_text(encoding="utf-8")
    assert "市场：A 股" in original and "2241期财报" in original
    markdown = build_report(symbol, name, results, commentary, context, no_llm=True)
    assert "市场：港股" in markdown
    assert "腾讯控股" in markdown
    assert "2241" not in markdown
    assert "同花顺财报" not in markdown
    assert "腾讯估值快照" not in markdown
    assert "14.23" in markdown and "2.9" in markdown
    assert "操作信号：" not in markdown
    if cash_flow_failure:
        assert "现金流量表" in markdown and "超时" in markdown
        cached = pipeline.collect(symbol, name, market="hk", data_types=["financial"])
        assert cached.collection_records["financial"].cache_status == "cache"
        assert "现金流量表" in cached.collection_records["financial"].error_message
    saved = ReportFormatter.save(markdown, symbol, category="stock", stock_payload=payload)
    assert saved.read_text(encoding="utf-8") == markdown
    saved_payload = json.loads(saved.with_suffix(".report.json").read_text(encoding="utf-8"))["payload"]
    assert saved_payload["market"] == "hk"
    assert saved_payload["dimensions"]["financial"]["metrics"]["revenue"] == 396431000000

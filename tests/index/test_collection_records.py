"""指数采集记录契约。"""
from datetime import date, datetime

import pytest

from data.base import CollectionResult
from data.schemas import (
    AnalysisTarget,
    CapitalFlowData,
    IndexPriceData,
    IndexValuationData,
    MacroContext,
    NewsData,
)
from index.collector import IndexDataCollector


def test_index_records_use_result_and_classify_fields(mocker):
    target = AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad")
    collector = IndexDataCollector(isolate_requests=False)
    rows = {
        "index_price": [IndexPriceData(symbol="000300", trade_date=date(2026, 1, 1), open=1, high=1, low=1, close=1, volume=1)],
        "index_valuation": [IndexValuationData(symbol="000300", date=date(2026, 1, 1), pe_ttm=10)],
        "index_capital_flow": [CapitalFlowData(symbol="000300", date=date(2026, 1, 1))],
        "index_macro": [MacroContext(symbol="000300", fetch_date=date(2026, 1, 1), pmi=50)],
        "index_sentiment": [NewsData(symbol="000300", date=date(2026, 1, 1), headlines=[])],
    }
    mocker.patch.object(collector._adapter, "fetch", side_effect=AssertionError("旧入口不得调用"))
    mocker.patch.object(collector._adapter, "fetch_result", side_effect=lambda symbol, **kw: CollectionResult(rows[kw["data_type"]]))
    ctx = collector.collect(target)
    assert {key: record.result_state for key, record in ctx.collection_records.items()} == {
        "index_price": "complete", "index_valuation": "partial", "index_capital_flow": "failed",
        "index_macro": "partial", "index_sentiment": "empty",
    }
    assert ctx.price_data and ctx.valuation_data is not None


def test_empty_or_exception_records_failed(mocker):
    target = AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad")
    collector = IndexDataCollector(isolate_requests=False)
    def fetch(symbol, **kw):
        if kw["data_type"] == "index_price":
            raise RuntimeError("网络异常")
        return CollectionResult([])
    mocker.patch.object(collector._adapter, "fetch_result", side_effect=fetch)
    ctx = collector.collect(target)
    assert len(ctx.collection_records) == 5
    assert all(record.result_state == "failed" for record in ctx.collection_records.values())


def test_adapter_sentiment_failure_is_not_successful_empty(mocker):
    from data.akshare import AkShareAdapter
    from data.collection_policies import index_policy_for

    mocker.patch("data.akshare.ak.stock_news_main_em", side_effect=RuntimeError("模拟失败"), create=True)
    result = AkShareAdapter().fetch_result("000300", data_type="index_sentiment")
    policy = index_policy_for("index_sentiment")
    assert policy is not None
    assert policy.classify("000300", result.rows, result.record, None, datetime.now().astimezone()) == "failed"


@pytest.mark.parametrize("headlines", [[""], ["   ", "\t"]])
def test_blank_sentiment_titles_are_empty(headlines):
    from data.collection_policies import index_policy_for

    policy = index_policy_for("index_sentiment")
    assert policy is not None
    news = NewsData(symbol="000300", date=date(2026, 1, 1), headlines=headlines)
    assert policy.classify("000300", [news], None, None, datetime.now().astimezone()) == "empty"

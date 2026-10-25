"""新舆情风险口径：以可追溯单条负面最高级别计分。"""
import json
from datetime import datetime, timedelta

import pytest

from analysis.sentiment import SentimentAnalyzer
from analysis.sentiment_rules import score_sentiment
from data.enrichers.sentiment_enricher import SentimentEnricher
from data.hk_news import HK_TZ
from data.schemas import (
    AnalysisContext,
    DataCollectionRecord,
    EnrichedSentiment,
    HKNewsItem,
    HKNewsLink,
    HKNewsSnapshot,
    NewsData,
    RawSentimentData,
    RawSentimentItem,
)

NOW = datetime(2026, 10, 25, 15, tzinfo=HK_TZ)


class BatchLLM:
    def __init__(self, change=None):
        self.calls = 0
        self.inputs = []
        self.change = change

    def generate(self, prompt):
        self.calls += 1
        self.inputs = json.loads(prompt.split("输入数据：\n", 1)[1])
        rows = [{"item_id": item["item_id"], "related": True, "assessable": True,
                 "tendency": "neutral", "severity": "minor", "evidence": item["title"]}
                for item in self.inputs]
        result = self.change(rows) if self.change else rows
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def context(market="a-shares", count=1):
    items = [RawSentimentItem(title=f"目标公司事项{i}", source="news", publish_date=NOW.date(),
                             content=f"事项{i}原文摘要", url=f"https://example.com/{i}", media="测试媒体")
             for i in range(count)]
    ctx = AnalysisContext(symbol="00700.HK" if market == "hk" else "600519", name="目标公司",
                          market=market, collected_at=NOW)
    if market == "hk":
        ctx.news_data = NewsData(symbol=ctx.symbol, date=NOW.date(), hk_news=HKNewsSnapshot(
            fetched_at=NOW, items=[HKNewsItem(title=item.title, content=item.content, published_at=NOW,
                links=[HKNewsLink(media=item.media, url=item.url)]) for item in items]))
    else:
        ctx.raw_sentiment = RawSentimentData(symbol=ctx.symbol, fetch_date=NOW.date(), items=items)
        ctx.news_data = NewsData(symbol=ctx.symbol, date=NOW.date(), headlines=[x.title for x in items])
    return ctx


@pytest.mark.parametrize("market", ["a-shares", "hk"])
@pytest.mark.parametrize(("tendency", "severity", "risk"), [
    ("neutral", "major", 0), ("positive", "major", 0), ("negative", "minor", 2),
    ("negative", "moderate", 5), ("negative", "major", 10),
])
def test_risk_mapping_and_single_batch(market, tendency, severity, risk):
    def change(rows):
        rows[0].update(tendency=tendency, severity=severity)
        return rows
    llm = BatchLLM(change)
    ctx = SentimentEnricher(llm).enrich(context(market))
    assert llm.calls == 1
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.risk_score == risk
    result = SentimentAnalyzer().analyze(ctx, config={"sentiment": {"base_score": 9}})
    assert result.score == risk
    assert result.metrics["participation_score"] == 10 - risk
    assert result.metrics["sentiment_scoring_version"] == "risk-v1"
    assert result.metrics["evaluated_count"] == 1
    assert result.metrics["window_start"] == "2026-09-26"
    assert result.metrics["window_end"] == "2026-10-25"
    assert result.metrics["all_items"][0]["url"] == "https://example.com/0"
    assert result.metrics["all_items"][0]["content"] == "事项0原文摘要"
    assert result.metrics["all_items"][0]["media"] == "测试媒体"


def test_negative_and_major_must_be_same_item_and_risks_do_not_add():
    def change(rows):
        rows[0].update(tendency="negative", severity="minor")
        rows[1].update(tendency="positive", severity="major")
        rows[2].update(tendency="negative", severity="moderate")
        return rows
    ctx = SentimentEnricher(BatchLLM(change)).enrich(context(count=3))
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.risk_score == 5
    assert "major_negative_news" not in SentimentAnalyzer().analyze(ctx).risk_flags


@pytest.mark.parametrize("change", [
    lambda rows: [], lambda rows: rows + rows, lambda rows: [{**rows[0], "item_id": "invented"}],
    lambda rows: [{key: value for key, value in rows[0].items() if key != "item_id"}],
    lambda rows: [{**rows[0], "evidence": "原文没有的犯罪事实"}],
    lambda rows: [{**rows[0], "related": "true"}],
    lambda rows: [{**rows[0], "tendency": "unknown"}],
    lambda rows: [{**rows[0], "severity": "critical"}], lambda rows: "{bad json",
    lambda rows: {"items": rows}, lambda rows: "（LLM 分析暂时不可用）",
])
def test_invalid_response_cannot_become_zero_risk(change):
    llm = BatchLLM(change)
    ctx = SentimentEnricher(llm).enrich(context())
    assert llm.calls == 1
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.risk_score is None
    assert ctx.enriched_sentiment.assessment_status == "failed"


@pytest.mark.parametrize("market", ["a-shares", "hk"])
def test_no_model_and_collection_failure_do_not_score(market):
    ctx = SentimentEnricher().enrich(context(market))
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.risk_score is None
    ctx = context(market)
    ctx.collection_records["news"] = DataCollectionRecord(error_kind="partial", error_message="公告失败")
    llm = BatchLLM()
    ctx = SentimentEnricher(llm).enrich(ctx)
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.risk_score is None
    assert "失败" in ctx.enriched_sentiment.assessment_reason


def test_unrelated_and_unassessable_are_not_neutral():
    for updates in ({"related": False}, {"assessable": False, "tendency": None, "severity": None}):
        def change(rows, updates=updates):
            rows[0].update(updates)
            return rows
        ctx = SentimentEnricher(BatchLLM(change)).enrich(context())
        assert ctx.enriched_sentiment is not None
        assert ctx.enriched_sentiment.risk_score is None
        assert ctx.enriched_sentiment.assessment_status == "unavailable"


def test_model_exception_is_failed_and_single_attempt():
    llm = BatchLLM(lambda _: (_ for _ in ()).throw(TimeoutError("模型超时")))
    ctx = SentimentEnricher(llm).enrich(context())
    assert llm.calls == 1
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.assessment_status == "failed"
    assert ctx.enriched_sentiment.risk_score is None


@pytest.mark.parametrize("market,limit", [("a-shares", 30), ("hk", 20)])
def test_window_and_limit_and_stable_input_ids(market, limit):
    ctx = context(market, count=40)
    if market == "hk":
        assert ctx.news_data is not None and ctx.news_data.hk_news is not None
        items = ctx.news_data.hk_news.items
        items[0].published_at = NOW - timedelta(days=29)
        items[1].published_at = NOW - timedelta(days=30)
        items[2].published_at = NOW + timedelta(days=1)
    else:
        assert ctx.raw_sentiment is not None
        items = ctx.raw_sentiment.items
        items[0].publish_date = (NOW - timedelta(days=29)).date()
        items[1].publish_date = (NOW - timedelta(days=30)).date()
        items[2].publish_date = (NOW + timedelta(days=1)).date()
    llm = BatchLLM()
    SentimentEnricher(llm).enrich(ctx)
    assert len(llm.inputs) == limit
    assert all(row["title"] not in {"目标公司事项1", "目标公司事项2"} for row in llm.inputs)
    first = llm.inputs
    SentimentEnricher(llm).enrich(ctx)
    assert llm.inputs == first


def test_partial_hk_search_and_stale_cache_are_unavailable():
    ctx = context("hk")
    assert ctx.news_data is not None and ctx.news_data.hk_news is not None
    ctx.news_data.hk_news.query_errors = ["公司名查询失败"]
    SentimentEnricher(BatchLLM()).enrich(ctx)
    assert ctx.enriched_sentiment is not None and ctx.enriched_sentiment.risk_score is None
    ctx = context()
    assert ctx.raw_sentiment is not None
    ctx.raw_sentiment._from_stale_cache = True
    SentimentEnricher(BatchLLM()).enrich(ctx)
    assert ctx.enriched_sentiment is not None and ctx.enriched_sentiment.risk_score is None


def test_unrelated_negative_does_not_raise_risk():
    def change(rows):
        rows[0].update(related=False, tendency="negative", severity="major")
        return rows
    ctx = SentimentEnricher(BatchLLM(change)).enrich(context(count=2))
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.risk_score == 0
    assert ctx.enriched_sentiment.evaluated_count == 1


def test_related_unassessable_prevents_zero_even_when_another_row_is_valid():
    def change(rows):
        rows[0].update(assessable=False, tendency=None, severity=None, evidence="")
        return rows
    ctx = SentimentEnricher(BatchLLM(change)).enrich(context(count=2))
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.risk_score is None
    assert ctx.enriched_sentiment.evaluated_count == 1
    assert "内容不足" in ctx.enriched_sentiment.assessment_reason


def test_repeated_id_same_batch_size_is_rejected():
    ctx = SentimentEnricher(BatchLLM(lambda rows: [rows[0], rows[0]])).enrich(context(count=2))
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.assessment_status == "failed"
    assert ctx.enriched_sentiment.risk_score is None


def test_model_cannot_replace_input_title_or_invent_evidence():
    for update in ({"title": "模型新标题"}, {"evidence": "<b>"}, {"evidence": ""}):
        def change(rows, update=update):
            rows[0].update(update)
            return rows
        ctx = SentimentEnricher(BatchLLM(change)).enrich(context())
        assert ctx.enriched_sentiment is not None
        assert ctx.enriched_sentiment.assessment_status == "failed"


def test_old_counts_and_labels_cannot_be_reinterpreted_as_risk():
    ctx = context()
    ctx.enriched_sentiment = EnrichedSentiment(total_count=30, positive_count=30, risk_score=0)
    assert score_sentiment(ctx)[0] is None
    assert SentimentAnalyzer().analyze(ctx, config={"sentiment": {"base_score": 5}}).score is None


def test_json_fence_and_original_html_entities_are_accepted():
    ctx = context()
    assert ctx.raw_sentiment is not None
    ctx.raw_sentiment.items[0].content = "公司现金&nbsp;充足，未出现损失"
    def change(rows):
        rows[0]["evidence"] = "现金 充足"
        return "```json\n" + json.dumps(rows, ensure_ascii=False) + "\n```"
    SentimentEnricher(BatchLLM(change)).enrich(ctx)
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.risk_score == 0


def test_no_valid_dates_never_calls_model_or_claims_zero():
    ctx = context()
    assert ctx.raw_sentiment is not None
    ctx.raw_sentiment.items[0].publish_date = (NOW - timedelta(days=30)).date()
    llm = BatchLLM()
    SentimentEnricher(llm).enrich(ctx)
    assert llm.calls == 0
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.risk_score is None

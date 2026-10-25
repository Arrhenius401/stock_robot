"""港股旧检索参考兼容及新五维质量边界。"""
from datetime import datetime, timedelta, timezone

from analysis.sentiment import SentimentAnalyzer
from data.enrichers.sentiment_enricher import SentimentEnricher
from data.schemas import (
    AnalysisContext,
    HKNewsItem,
    HKNewsLink,
    HKNewsSnapshot,
    NewsData,
)
from report.builder import ReportBuilder
from report.payload import build_stock_payload
from report.quality import build_data_quality

NOW = datetime.now(timezone(timedelta(hours=8)))


def context(count=22, *, errors=None):
    snapshot = HKNewsSnapshot(fetched_at=NOW, queries=["00700", "腾讯控股"],
        query_errors=errors or [], items=[HKNewsItem(title=f"新闻 {i}", published_at=NOW - timedelta(minutes=i),
            links=[HKNewsLink(media="测试媒体", url=f"https://example.com/{i}")]) for i in range(count)])
    return AnalysisContext(symbol="00700.HK", name="腾讯", market="hk", news_data=NewsData(
        symbol="00700.HK", date=NOW.date(), hk_news=snapshot))


def test_hk_legacy_news_twenty_items_remain_unscored_without_new_assessment():
    result = SentimentAnalyzer().analyze(context(), config={"enabled": True})
    assert result.score is None
    assert result.status == "ok"
    assert len(result.metrics["news_items"]) == 20
    assert "关键词检索结果" in result.metrics["news_disclaimer"]
    assert "不保证完整覆盖" in result.metrics["news_disclaimer"]


def test_hk_news_empty_failure_and_partial_differ():
    empty = SentimentAnalyzer().analyze(context(0))
    assert "本次检索未获得近30天有效记录" in empty.summary
    failed = SentimentAnalyzer().analyze(AnalysisContext(symbol="00700.HK", name="腾讯", market="hk"))
    assert failed.status == "unavailable"
    assert "获取失败" in failed.summary
    partial = SentimentAnalyzer().analyze(context(2, errors=["00700: timeout"]))
    assert partial.status == "partial"
    assert "检索不完整" in "；".join(partial.metrics["news_notes"])


def test_stale_preserves_original_timestamp_and_empty_is_not_fresh_success():
    ctx = context(0)
    assert ctx.news_data is not None
    ctx.news_data._from_stale_cache = True
    result = SentimentAnalyzer().analyze(ctx)
    assert result.status == "partial"
    assert "获取失败" in result.summary
    assert result.metrics["news_fetched_at"] == NOW.isoformat()


def test_missing_legacy_snapshot_never_displays_headlines_as_verified_news():
    ctx = context(0)
    assert ctx.news_data is not None
    ctx.news_data.hk_news = None
    ctx.news_data.headlines = ["旧缓存不可信新闻"]
    result = SentimentAnalyzer().analyze(ctx)
    assert result.status == "unavailable"
    assert result.metrics["news_items"] == []


def test_hk_enricher_model_failure_retains_reference_without_scoring():
    class Model:
        def generate(self, prompt):
            raise TimeoutError("舆情标注超时")
    ctx = context()
    SentimentEnricher(llm=Model()).enrich(ctx)
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.assessment_status == "failed"
    assert ctx.enriched_sentiment.risk_score is None
    assert ctx.sufficiency.sentiment.score_weight == 0


def test_hk_quality_reference_and_payload_markdown():
    ctx = context(2)
    result = SentimentAnalyzer().analyze(ctx)
    quality = build_data_quality(ctx, [result])
    assert quality["applicable_dimensions"] == 5
    assert quality["coverage"] == 0
    news = next(row for row in quality["dimensions"] if row["key"] == "sentiment")
    assert news["status"] != "not_applicable"
    assert news["title"] == "舆情"
    assert not quality["has_scored_data"]
    payload = build_stock_payload(ctx.symbol, "腾讯", [result], {}, ctx, no_llm=True)
    assert payload["dimensions"]["sentiment"]["metrics"]["news_items"] == result.metrics["news_items"]
    markdown = ReportBuilder().build(ctx.symbol, "腾讯", [result], {}, market="港股", no_llm=True,
        data_quality=quality)
    assert "https://example.com/0" not in markdown
    assert "测试媒体" in markdown
    assert NOW.strftime("%Y-%m-%d %H:%M").replace("-", "\\-") in markdown
    assert "关键词检索结果" in markdown
    assert "原采集时间" not in markdown


def test_cached_news_expiry_and_unsafe_links_hidden():
    ctx = context(2)
    assert ctx.news_data is not None
    snapshot = ctx.news_data.hk_news
    assert snapshot is not None
    snapshot.items[0].published_at = NOW - timedelta(days=31)
    snapshot.items[1].links[0].url = "javascript:alert(1)"
    result = SentimentAnalyzer().analyze(ctx)
    assert result.metrics["news_items"] == []


def test_news_markdown_escapes_untrusted_text():
    ctx = context(1)
    assert ctx.news_data is not None
    snapshot = ctx.news_data.hk_news
    assert snapshot is not None
    snapshot.items[0].title = '<img src=x onerror=alert(1)> [假链接](javascript:bad)'
    snapshot.items[0].links[0].media = '[媒体](javascript:bad)'
    result = SentimentAnalyzer().analyze(ctx)
    markdown = ReportBuilder().build(ctx.symbol, "腾讯", [result], {}, market="港股", no_llm=True)
    assert "<img src=x" not in markdown
    assert "[假链接](javascript:bad)" not in markdown


def test_news_markdown_uses_plain_list_without_source_links():
    from report.formatter import format_hk_news_markdown

    text = format_hk_news_markdown({"news_items": [{"title": "公司回购股份",
        "published_at": "2026-10-23T18:21:00+08:00", "links": [
            {"media": "媒体甲", "url": "https://example.com/a"},
            {"media": "媒体乙", "url": "https://example.com/b"},
            {"media": "媒体甲", "url": "https://example.com/c"}]}]})
    assert "- 公司回购股份（2026\\-10\\-23 18:21 · 媒体甲、媒体乙）" in text
    assert "https://" not in text
    assert "](" not in text
    assert "T18:21" not in text


def test_news_diagnostics_remain_internal_not_in_report():
    ctx = context(2, errors=["00700: timeout"])
    assert ctx.news_data is not None and ctx.news_data.hk_news is not None
    ctx.news_data.hk_news.limit_reached = True
    ctx.news_data._from_stale_cache = True
    result = SentimentAnalyzer().analyze(ctx)
    assert result.metrics["news_fetched_at"] == NOW.isoformat()
    assert len(result.metrics["news_notes"]) == 3
    quality = build_data_quality(ctx, [result])
    markdown = ReportBuilder().build(ctx.symbol, "腾讯", [result], {}, market="港股",
        no_llm=True, data_quality=quality)
    for diagnostic in ("原采集时间", "部分关键词", "候选上限", "刷新失败"):
        assert diagnostic not in markdown
    assert "新闻 0" in markdown
    assert "测试媒体" in markdown


def test_news_empty_and_failure_still_explained_in_report():
    for ctx, expected in ((context(0), "本次检索未获得近30天有效记录"),
            (AnalysisContext(symbol="00700.HK", name="腾讯", market="hk"), "新闻暂时获取失败")):
        result = SentimentAnalyzer().analyze(ctx)
        markdown = ReportBuilder().build(ctx.symbol, "腾讯", [result], {}, market="港股", no_llm=True)
        assert expected in markdown

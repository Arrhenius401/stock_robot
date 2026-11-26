"""SEC 公告离线契约；合成 submissions 样本，不依赖实时网络。"""
import requests

from data.us_filings import fetch_filings, parse_submissions


def payload():
    return {"cik": "320193", "filings": {"recent": {
        "form": ["8-K", "4", "10-Q"],
        "filingDate": ["2026-11-13", "2026-11-12", "2026-11-01"],
        "accessionNumber": ["0000320193-26-000001", "bad", "0000320193-26-000002"],
        "primaryDocument": ["a.htm", "bad", "q.htm"]}}}


def test_parse_metadata_and_filter():
    rows = parse_submissions(payload(), limit=5)
    assert len(rows) == 2
    assert rows[0]["form"] == "8-K"
    assert rows[0]["filingDate"] == "2026-11-13"
    assert rows[0]["accessionNumber"] == "0000320193-26-000001"
    assert rows[0]["url"] == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/a.htm"
    assert parse_submissions(payload(), limit=0) == []


def test_body_failure_preserves_metadata(mocker):
    mocker.patch("data.us_filings.resolve_cik", return_value=320193)
    response = mocker.Mock()
    response.json.return_value = payload()
    mocker.patch("data.us_filings.requests.get", side_effect=[response, requests.ConnectionError(), requests.ConnectionError()])
    rows = fetch_filings("AAPL")
    assert len(rows) == 2
    assert rows[0]["content"] == ""
    assert rows[0]["url"].endswith("/a.htm")


def test_html_body_is_bounded_and_scripts_removed(mocker):
    mocker.patch("data.us_filings.resolve_cik", return_value=320193)
    response = mocker.Mock()
    response.json.return_value = payload()
    body = mocker.Mock(content=b"<script>hidden</script><p>" + b"a"*1500 + b"</p>")
    mocker.patch("data.us_filings.requests.get", side_effect=[response, body, body])
    rows = fetch_filings("AAPL")
    assert rows[0]["content"] == "a"*1000


def test_cik_mapping_cache_and_share_class(mocker, tmp_path):
    from data.cache import CacheManager
    from data.us_filings import resolve_cik

    cache = CacheManager(tmp_path / "sec.db")
    mocker.patch("data.us_filings._source_cache", return_value=cache)
    response = mocker.Mock()
    response.json.return_value = {"0": {"ticker": "BRK-B", "cik_str": 1067983}}
    request = mocker.patch("data.us_filings.requests.get", return_value=response)
    assert resolve_cik("BRK.B") == 1067983
    request.side_effect = AssertionError("缓存有效时不应访问网络")
    assert resolve_cik("BRK-B") == 1067983
    entry = cache.get_entry("sec_tickers", "all", "v1")
    assert entry is not None and entry.ttl_seconds == 30*86400


def test_sec_metadata_survives_news_failure_and_remains_unscored(mocker):
    from analysis.sentiment import SentimentAnalyzer
    from data.schemas import AnalysisContext
    from data.us_stock import us_news

    rows = parse_submissions(payload())
    mocker.patch("data.us_filings.fetch_filings", return_value=rows)
    mocker.patch("data.us_stock.search_news", side_effect=RuntimeError("offline"))
    news = us_news("AAPL", "苹果")[0]
    assert news.announcement_items == rows
    ctx = AnalysisContext(symbol="AAPL", name="苹果", market="us", news_data=news,
                          extra_analysis_enabled=True)
    result = SentimentAnalyzer().analyze(ctx)
    assert result.score is None
    assert result.metrics["news_items"][0]["url"] == rows[0]["url"]
    assert result.metrics["news_items"][0]["source"] == "SEC EDGAR"

    from report.formatter import format_news_facts_markdown

    ordinary = [{"title": f"苹果新闻 {i}", "published_at": "2026-11-13",
                 "links": [{"url": f"https://example.com/{i}", "media": "新闻"}]}
                for i in range(10)]
    mocker.patch("analysis.sentiment.assessment_inputs", return_value=ordinary)
    result = SentimentAnalyzer().analyze(ctx)
    assert result.metrics["news_items"][0]["source"] == "SEC EDGAR"
    assert rows[0]["url"] in format_news_facts_markdown(result.metrics)

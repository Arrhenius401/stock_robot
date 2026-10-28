"""旧数据只在质量说明中作参考，不进入本次事实、评分和模型上下文。"""
import html
import json
from copy import deepcopy
from datetime import UTC, date, datetime
from html.parser import HTMLParser

from markdown_it import MarkdownIt

from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    DataCollectionRecord,
    DataReference,
    FinancialData,
    NewsData,
    PriceData,
    RawSentimentData,
    RawSentimentItem,
)
from report.payload import build_stock_payload
from report.quality import build_data_quality
from report.scoring import build_report
from report.signal import SignalConfig
from tests.api.test_static import _DOM_STUB, _module_url, _run_node

STAMP = datetime(2026, 10, 20, 12, tzinfo=UTC)


def reference_context() -> AnalysisContext:
    financial = DataReference(data_type="financial", record=DataCollectionRecord(
        source="原财报来源", collected_at=STAMP), reason="本次财报刷新失败",
        financial_data=[FinancialData(symbol="000001", fiscal_quarter=date(2026, 6, 30),
            revenue=123456, net_profit=-12, total_assets=500000, total_equity=0,
            operating_cash_flow=None, report_currency="CNY")])
    price = DataReference(data_type="price", record=DataCollectionRecord(
        source="原日线来源", collected_at=STAMP), reason="本次行情刷新失败",
        price_data=[PriceData(symbol="000001", trade_date=date(2026, 10, 19),
                             open=10, high=11, low=9, close=10.5, volume=100),
                    PriceData(symbol="000001", trade_date=date(2026, 10, 16),
                             open=10, high=11, low=9, close=10, volume=100)])
    return AnalysisContext(symbol="000001", name="参考回归", reference_data={
        "financial": financial, "price": price})


def fields(quality, dimension):
    return {field["label"]: field["value"] for row in quality["dimensions"]
            if row["key"] == dimension for field in row["fields"]}


def test_reference_only_displays_facts_without_creating_score_signal_or_current_cutoff():
    ctx = reference_context()
    original = deepcopy(ctx)
    payload = build_stock_payload(ctx.symbol, ctx.name, [], {}, ctx, no_llm=True,
                                  signal_cfg=SignalConfig())
    quality = payload["data_quality"]
    assert not quality["has_analysis_data"] and not quality["has_scored_data"]
    assert payload["score"]["final"] is None and payload["signal"] is None
    assert ctx.financial_data is None and ctx.price_data is None
    financial = fields(quality, "financial")
    assert "本次财报刷新失败" in financial["历史参考（不计分）"]
    assert STAMP.isoformat() == financial["参考原采集时间"]
    assert "2026-06-30" in financial["参考报告期"]
    assert "净利润 -12.00" in financial["参考财务指标"]
    assert "净资产 0.00" in financial["参考财务指标"]
    assert "经营现金流" not in financial["参考财务指标"]
    assert "CNY" in financial["参考财务指标"]
    assert financial["数据截止"] == "未记录"
    price = fields(quality, "technical")
    assert "2026-10-16 至 2026-10-19" in price["参考交易区间"]
    assert "2" in price["参考交易区间"] and "10.50" == price["参考末收盘价"]
    assert ctx == original


def test_current_partial_and_reference_have_separate_periods_and_sources():
    ctx = reference_context()
    ctx.financial_data = [FinancialData(symbol=ctx.symbol, fiscal_quarter=date(2026, 9, 30), revenue=200)]
    ctx.collection_records["financial"] = DataCollectionRecord(source="本次财报来源", collected_at=STAMP)
    result = AnalysisResult(dimension="financial", status="partial", summary="本次财报部分缺失", score=5)
    quality = build_data_quality(ctx, [result])
    financial = fields(quality, "financial")
    assert financial["数据截止"] == "2026-09-30" and financial["数据来源"] == "本次财报来源"
    assert financial["参考报告期"] == "2026-06-30"
    assert financial["参考数据来源"] == "原财报来源"
    assert result.score == 5 and ctx.financial_data[0].total_assets is None


def test_model_quality_view_omits_references_and_legacy_output_stays_unchanged():
    ctx = reference_context()
    without = ctx.model_copy(update={"reference_data": {}})
    assert build_data_quality(ctx, [], include_references=False) == build_data_quality(without, [])
    assert "历史参考" not in json.dumps(build_data_quality(ctx, [], include_references=False), ensure_ascii=False)
    assert build_data_quality(without, []) == build_data_quality(without, [], include_references=False)


def news_context(title):
    row = NewsData(symbol="000001", date=date(2026, 10, 20), headlines=[title])
    row._raw_sentiment = RawSentimentData(symbol=row.symbol, fetch_date=row.date,
        items=[RawSentimentItem(title=title if index == 0 else f"消息{index}", source="news",
                               publish_date=date(2026, 10, 19), content="原摘要") for index in range(7)])
    return AnalysisContext(symbol=row.symbol, name="参考回归", reference_data={"news": DataReference(
        data_type="news", record=DataCollectionRecord(source="媒体-A_[原文]", collected_at=STAMP),
        news_data=row, reason="公告请求失败")})


class Markup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_data(self, data):
        self.text.append(data)


def test_news_reference_has_real_publication_dates_limit_and_safe_markdown():
    title = r"2026-10-19 *标题* [伪链接](javascript:bad) <img src=x onerror=alert(1)> C:\reports\a_b.md | 竖线"
    ctx = news_context(title)
    quality = build_data_quality(ctx, [])
    sentiment = fields(quality, "sentiment")
    messages = [value for key, value in sentiment.items() if key.startswith("参考消息 ")]
    assert len(messages) == 5 and title in messages[0] and "2026-10-19" in messages[0]
    assert "消息5" not in " ".join(messages)
    assert sentiment["数据截止"] == "未记录"
    assert sentiment["参考消息日期"] == "2026-10-19"
    markdown = build_report(ctx.symbol, ctx.name, [], {}, ctx, no_llm=True)
    parsed = Markup()
    parsed.feed(MarkdownIt().render(markdown))
    assert title in "".join(parsed.text)
    assert not set(parsed.tags) & {"img", "script", "a", "em", "code"}
    assert "参考消息 1" in html.unescape(markdown)


def test_reference_news_never_uses_fetch_date_as_publication_date():
    ctx = news_context("只有旧标题")
    row = ctx.reference_data["news"].news_data
    assert row is not None
    row._raw_sentiment = None
    quality = build_data_quality(ctx, [])
    sentiment = fields(quality, "sentiment")
    assert sentiment["参考消息日期"] == "未记录"
    assert "发布日期未记录" in sentiment["参考消息 1"]
    assert "2026-10-20" not in sentiment["参考消息 1"]


def test_web_existing_quality_fields_keep_reference_news_plain_text(tmp_path):
    title = "*普通星号* <img src=x onerror=alert(1)> [伪链接](javascript:bad)"
    ctx = news_context(title)
    payload = build_stock_payload(ctx.symbol, ctx.name, [], {}, ctx, no_llm=True)
    script = _DOM_STUB + "\nconst payload=" + json.dumps(payload, ensure_ascii=False) + ";\n"
    script += "const {renderStockReport}=await import(" + json.dumps(_module_url("src/api/static/js/report-renderer.js")) + ");\n"
    script += r'''
const report=renderStockReport(payload);
const nodes=descendants(report);
if(!nodes.some(node=>node.tagName==='DD'&&node.textContent.includes(__TITLE__)))throw new Error('参考标题原文丢失');
if(nodes.some(node=>['IMG','SCRIPT','A','EM','CODE'].includes(node.tagName)))throw new Error('参考纯文本被当成HTML');
if(!report.textContent.includes('历史参考（不计分）'))throw new Error('没有参考状态');
'''.replace("__TITLE__", json.dumps(title, ensure_ascii=False))
    _run_node(tmp_path, script)

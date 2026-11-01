"""资讯纯文本兼容标准 Markdown 与 AstrBot 本地实体解码。"""
import html
import json
from copy import deepcopy
from html.parser import HTMLParser

import pytest
from markdown_it import MarkdownIt

from report.formatter import (
    _news_text,
    format_hk_news_markdown,
    format_sentiment_risk_markdown,
)
from tests.api.test_static import _module_url, _run_node


class ParsedMarkup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.tags: list[str] = []
        self.links: list[str] = []

    def handle_data(self, data: str):
        self.text.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        self.tags.append(tag)
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href" and value is not None)


@pytest.mark.parametrize("raw", [
    "2026-10-16", "2026-10-16T12:30:00+08:00",
    "千亿级回购：腾讯带头，科技股“自救”", "百度集团-W (09888.HK)",
    r"C:\reports\2026-10-16\a_b.md", r"真实反斜杠\-与普通连字符-",
    "*星号* **加粗** _下划线_ `代码` ~~删除~~",
    "[伪链接](javascript:bad) ![图片](https://example.com/a.png)",
    "<img src=x onerror=alert(1)> & &#91;原实体&#93;",
    "# 假标题 | 表格 + 加号 {花括号} > 引用 !",
    "第一行\n第二行\r第三行",
])
def test_news_text_roundtrips_without_backslash_artifacts_or_markup_injection(raw):
    expected = raw.replace("\n", " ").replace("\r", " ")
    escaped = _news_text(raw)
    assert "\\" not in escaped
    # AstrBot 本地解析器在识别 Markdown 标记后只做一次 html.unescape。
    assert html.unescape(escaped) == expected
    parsed = ParsedMarkup()
    parsed.feed(MarkdownIt().render(escaped))
    assert "".join(parsed.text).rstrip("\n") == expected
    assert not set(parsed.tags) & {"img", "script", "a", "em", "strong", "code", "h1"}


@pytest.mark.parametrize("formatter", [format_hk_news_markdown, format_sentiment_risk_markdown])
def test_full_news_output_keeps_title_date_media_evidence_and_real_backslash(formatter):
    title = r"百度集团-W (09888.HK) *公告* C:\reports\a_b.md"
    metrics = {"window_start": "2026-09-26", "window_end": "2026-10-25", "evaluated_count": 1,
        "all_items": [{"item_id": "one", "title": title, "publish_date": "2026-10-16",
            "related": True, "assessable": True, "evidence": "公告(第一期)-已披露"}],
        "news_items": [{"item_id": "one", "title": title, "published_at": "2026-10-16T12:30:00+08:00",
            "links": [{"url": "https://example.com/news?q=1&x=2", "media": "媒体-A_[原文]"}]}]}
    original = deepcopy(metrics)
    markdown = formatter(metrics)
    assert "\\" not in markdown
    decoded = html.unescape(markdown)
    assert title in decoded and "2026-10-16" in decoded and "媒体-A_[原文]" in decoded
    if formatter is format_sentiment_risk_markdown:
        assert "2026-09-26" in decoded and "公告(第一期)-已披露" in decoded
        parsed = ParsedMarkup()
        parsed.feed(MarkdownIt().render(markdown))
        assert parsed.links == ["https://example.com/news?q=1&x=2"]
        assert "无法评估" in "".join(parsed.text)
    assert metrics == original


def test_web_markdown_preserves_text_entities_but_keeps_code_literal_and_html_inert(tmp_path):
    raw = r"百度集团-W (09888.HK) *星号* C:\reports\a_b.md <img src=x onerror=alert(1)>"
    script = "const {renderMarkdown}=await import(" + json.dumps(_module_url("src/api/static/js/markdown.js")) + ");\n"
    script += "const encoded=" + json.dumps(_news_text(raw), ensure_ascii=False) + ";\n"
    script += r'''
const output=renderMarkdown(encoded);
if(output.includes('&amp;#45;')||!output.includes('&#45;'))throw new Error('新闻实体被二次转义');
if(output.includes('<img')||output.includes('<em>')||output.includes('<script'))throw new Error('外部文本被当成标记执行');
const inline=renderMarkdown('代码 `&#45; &lt;img&gt;`');
if(!inline.includes('<code>&amp;#45; &amp;lt;img&amp;gt;</code>'))throw new Error('行内代码实体被解码');
const fenced=renderMarkdown('```\n&#45; &lt;img&gt;\n```');
if(!fenced.includes('&amp;#45; &amp;lt;img&amp;gt;'))throw new Error('代码块实体被解码');
const attack=renderMarkdown('&#60;script&#62;alert(1)&#60;/script&#62; [危险](javascript:alert)');
if(attack.includes('<script')||attack.includes('<a '))throw new Error('实体绕过了HTML或链接限制');
'''
    _run_node(tmp_path, script)

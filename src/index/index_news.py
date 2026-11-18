"""港美指数相关市场新闻，保留可核查原始出处。"""
import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from data.hk_news import search_news
from index.news import news_text, publication_time

NEWS_DISCLAIMER = "市场新闻，不等于指数专属事件"
_MARKET_TERMS = {
    "hk": ("港股", "香港股市", "恒生", "恒科", "hstech", "hscei"),
    "us": ("美股", "美国股市", "华尔街", "标普", "纳指", "纳斯达克", "道指", "道琼斯", "s&p 500"),
}
_ALIAS = {
    "SPX": "标普500", "IXIC": "纳斯达克指数", "DJI": "道琼斯指数",
    "HSI:02083.00": "恒生科技指数", "HSTECH": "恒生科技指数",
    "HSI": "恒生指数", "HSCEI": "恒生中国企业指数",
}
_AD = re.compile(r"etf|基金|联接|净值|标的指数", re.IGNORECASE)
_HK_TZ = timezone(timedelta(hours=8))


def _valid_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and not parsed.username and not parsed.password
                and not any(char.isspace() or ord(char) < 32 for char in url))
    except ValueError:
        return False


def search_index_news(symbol: str, name: str, market: str) -> list[dict[str, str]]:
    terms = _MARKET_TERMS.get(market)
    if terms is None:
        return []
    keyword = name.strip() or _ALIAS.get(symbol) or symbol
    if not keyword:
        return []
    aliases = (*terms, keyword, _ALIAS.get(symbol, ""), symbol if symbol.isalpha() else "")
    candidates = []
    now = datetime.now(_HK_TZ)
    for row in search_news(keyword):
        title = " ".join(html.unescape(re.sub(r"<[^>]*>", "", news_text(row, "title"))).split())
        if not title or _AD.search(title) or not any(alias and alias.casefold() in title.casefold() for alias in aliases):
            continue
        link = news_text(row, "url")
        stamp = publication_time({"published_at": row.get("date")})
        if not stamp or not _valid_url(link):
            continue
        try:
            published = datetime.fromisoformat(stamp.replace("/", "-"))
            published = published.astimezone(_HK_TZ) if published.tzinfo is not None else published.replace(tzinfo=_HK_TZ)
        except ValueError:
            continue
        if published > now:
            continue
        candidates.append((published, title, link, news_text(row, "mediaName") or "未记录"))

    items, titles, links = [], set(), set()
    for published, title, link, source in sorted(candidates, key=lambda item: item[0], reverse=True):
        if title.casefold() in titles or link in links:
            continue
        titles.add(title.casefold())
        links.add(link)
        items.append({"title": title, "source": source, "published_at": published.isoformat(),
                      "url": link, "news_disclaimer": NEWS_DISCLAIMER})
        if len(items) == 10:
            break
    return items

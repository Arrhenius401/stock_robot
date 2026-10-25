"""港股免费关键词资讯：限量检索、日期过滤和明确去重，不作语义判断。"""
import html
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from curl_cffi import requests

from data.schemas import HKNewsItem, HKNewsLink, HKNewsSnapshot

logger = logging.getLogger(__name__)
HK_TZ = timezone(timedelta(hours=8))
SOURCE = "东方财富关键词新闻检索"


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", value))).strip()


def search_news(keyword: str) -> list[dict[str, Any]]:
    """沿用官网资讯参数，单关键词单请求，不翻页、不重试。"""
    query = {"uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"],
             "client": "web", "clientType": "web", "clientVersion": "curr",
             "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "time",
                       "pageIndex": 1, "pageSize": 100, "preTag": "<em>", "postTag": "</em>"}}}
    response = requests.get("https://search-api-web.eastmoney.com/search/jsonp",
        params={"cb": "hkNews", "param": json.dumps(query, ensure_ascii=False)},
        headers={"Referer": "https://so.eastmoney.com/news/s"}, timeout=12)
    response.raise_for_status()
    raw = response.text.strip().removesuffix(";").strip()
    if not raw.startswith("hkNews(") or not raw.endswith(")"):
        raise ValueError("资讯返回格式无效")
    payload: Any = json.loads(raw[len("hkNews("):-1])
    if not isinstance(payload, dict) or payload.get("bizCode") not in (None, "", "0", 0):
        raise ValueError("资讯搜索业务返回失败")
    result = payload.get("result")
    rows = result.get("cmsArticleWebOld") if isinstance(result, dict) else None
    if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
        raise ValueError("资讯搜索记录格式或数量异常")
    return rows


def _safe_link(link: HKNewsLink) -> bool:
    try:
        parts = urlsplit(link.url)
        return (parts.scheme in {"https", "http"} and bool(parts.hostname)
                and not parts.username and not parts.password
                and not any(ord(char) < 32 for char in link.url))
    except ValueError:
        return False


def current_news(snapshot: HKNewsSnapshot, now: datetime | None = None) -> HKNewsSnapshot:
    """每次展示按香港日期重过滤，缓存采集时间不被改成当前时间。"""
    now = (now or datetime.now(HK_TZ)).astimezone(HK_TZ)
    start = now.date() - timedelta(days=29)
    items = []
    for item in snapshot.items:
        if item.published_at.tzinfo is None:
            continue
        stamp = item.published_at.astimezone(HK_TZ)
        if not start <= stamp.date() <= now.date() or stamp > now:
            continue
        links = [link.model_copy() for link in item.links if _safe_link(link)]
        if item.title.strip() and links:
            items.append(item.model_copy(update={"links": links}))
    items.sort(key=lambda item: item.published_at, reverse=True)
    return snapshot.model_copy(update={"items": items})


def normalize_news(rows: list[dict[str, Any]], now: datetime) -> list[HKNewsItem]:
    """只清洗、合并确定相同的记录；不把近似标题视为同一事件。"""
    items = []
    for row in rows:
        try:
            stamp = datetime.fromisoformat(str(row.get("date", "")))
            stamp = stamp.replace(tzinfo=HK_TZ) if stamp.tzinfo is None else stamp.astimezone(HK_TZ)
            title = _clean(str(row.get("title") or ""))
            code = str(row.get("code") or "")
            if not title or not re.fullmatch(r"[0-9]+", code):
                continue
            link = HKNewsLink(
                media=_clean(str(row.get("mediaName") or "来源未标注")),
                url=str(row.get("url") or f"https://finance.eastmoney.com/a/{code}.html"))
            if not _safe_link(link):
                continue
            items.append(HKNewsItem(title=title, content=_clean(str(row.get("content") or "")),
                                    published_at=stamp, links=[link]))
        except (ValueError, TypeError) as error:
            logger.debug("跳过日期或字段无效的港股新闻：%s", error)
    snapshot = current_news(HKNewsSnapshot(fetched_at=now, items=items), now)
    # 并查集合并交叉重复：同链接与同日同标题可关联到同一个组。
    parents = list(range(len(snapshot.items)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    seen: dict[tuple, int] = {}
    for index, item in enumerate(snapshot.items):
        for key in [("url", item.links[0].url), ("title", item.published_at.date(), item.title)]:
            if key in seen:
                left, right = root(index), root(seen[key])
                parents[max(left, right)] = min(left, right)
            seen[key] = index
    groups: dict[int, HKNewsItem] = {}
    for index, item in enumerate(snapshot.items):
        key = root(index)
        if key not in groups:
            groups[key] = item.model_copy(deep=True)
        else:
            target = groups[key]
            if not target.content and item.content:
                target.content = item.content
            for link in item.links:
                if link not in target.links:
                    target.links.append(link)
    return list(groups.values())


def fetch_news(symbol: str, name: str, *, now: datetime | None = None) -> HKNewsSnapshot:
    """最多两并发查询；部分失败保留已取结果，全部失败交给管道缓存兜底。"""
    code = symbol.removesuffix(".HK")
    if not re.fullmatch(r"[0-9]{5}", code):
        raise ValueError("港股代码必须为五位数字")
    queries = [code]
    name = name.strip()
    missing_name = name in {"", symbol, code} or name.isdigit()
    if not missing_name:
        queries.append(name)
    errors, rows = [], []
    successes, limit_reached = 0, False
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [(query, executor.submit(search_news, query)) for query in queries]
        for query, future in futures:
            try:
                records = future.result()
                successes += 1
                rows.extend(records)
                limit_reached |= len(records) == 100
            except Exception as error:  # noqa: BLE001 — 单查询网络边界隔离，保留部分成功
                logger.warning("港股新闻查询失败（%s）：%s", query, type(error).__name__)
                errors.append(query)
    if not successes:
        raise RuntimeError("港股新闻暂时获取失败")
    if missing_name:
        errors.append("公司名称未提供，仅按代码检索")
    fetched_at = now or datetime.now(HK_TZ)
    return HKNewsSnapshot(fetched_at=fetched_at, queries=queries, query_errors=errors,
        candidate_count=len(rows), limit_reached=limit_reached, items=normalize_news(rows, fetched_at))

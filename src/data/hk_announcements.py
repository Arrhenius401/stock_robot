"""HKEXnews 公告：元数据优先，PDF 失败独立降级。"""
import json
import logging
import re
import time
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from pypdf import PdfReader

from data.hk_news import HK_TZ

logger = logging.getLogger(__name__)
BASE = "https://www1.hkexnews.hk"
_STOCK_IDS: dict[str, tuple[float, int]] = {}


def parse_stock_id(text: str) -> int:
    match = re.search(r'"stockId"\s*:\s*(\d+)', text)
    if match is None:
        raise ValueError("HKEXnews 未返回证券 ID")
    return int(match[1])


def lookup_stock_id(code: str) -> int:
    code = code.removesuffix(".HK")
    if not re.fullmatch(r"\d{5}", code):
        raise ValueError("港股代码必须为五位数字")
    cached = _STOCK_IDS.get(code)
    if cached and time.monotonic() - cached[0] < 30 * 86400:
        return cached[1]
    response = requests.get(BASE + "/search/prefix.do", params={
        "callback": "callback", "lang": "ZH", "type": "A", "name": code, "market": "SEHK"}, timeout=12)
    response.raise_for_status()
    stock_id = parse_stock_id(response.text)
    _STOCK_IDS[code] = (time.monotonic(), stock_id)
    return stock_id


def parse_announcements(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = json.loads(payload["result"])
    if not isinstance(rows, list):
        raise TypeError("HKEXnews 公告列表格式异常")
    items = []
    for row in rows[:20]:
        try:
            url = urljoin(BASE, row["FILE_LINK"])
            if urlsplit(url).hostname != "www1.hkexnews.hk" or urlsplit(url).scheme != "https":
                continue
            stamp = datetime.strptime(row["DATE_TIME"], "%d/%m/%Y %H:%M").replace(tzinfo=HK_TZ)
            if row["TITLE"].strip():
                items.append({"title": row["TITLE"], "type": row.get("LONG_TEXT", ""),
                              "published_at": stamp, "url": url})
        except (ValueError, KeyError, TypeError) as exc:
            logger.debug("跳过无效公告：%s", exc)
    return items


def fetch_announcement_text(url: str) -> str:
    try:
        if urlsplit(url).hostname != "www1.hkexnews.hk" or urlsplit(url).scheme != "https":
            return ""
        response = requests.get(url, timeout=12)
        response.raise_for_status()
        reader = PdfReader(BytesIO(response.content))
        text = ""
        for page in reader.pages[:5]:
            text += page.extract_text() or ""
            if len(text) >= 1000:
                break
        return text[:1000]
    except Exception as exc:  # noqa: BLE001 — PDF/网络边界，正文失败保留元数据
        logger.warning("公告正文提取失败 %s：%s", url, type(exc).__name__)
        return ""


def fetch_announcements(code: str, days: int = 45) -> list[dict[str, Any]]:
    stock_id = lookup_stock_id(code)
    now = datetime.now(HK_TZ)
    start = now - timedelta(days=days)
    response = requests.get(BASE + "/search/titleSearchServlet.do", params={
        "sortDir": "0", "sortByOptions": "DateTime", "category": "0", "market": "SEHK",
        "stockId": str(stock_id), "r": "1", "lang": "ZH", "rowRange": "20",
        "fromDate": start.strftime("%Y%m%d"), "toDate": now.strftime("%Y%m%d"),
        "searchType": "0", "documentType": "-1", "t1code": "-2", "t2Gcode": "-2",
        "t2code": "-2", "title": ""}, timeout=12)
    response.raise_for_status()
    items = [item for item in parse_announcements(response.json())
             if start.date() <= item["published_at"].date() <= now.date() and item["published_at"] <= now]
    items.sort(key=lambda item: item["published_at"], reverse=True)
    for item in items[:5]:
        item["content"] = fetch_announcement_text(item["url"])
    return items

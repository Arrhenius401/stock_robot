"""A 股公告正文：失败由采集边界隔离，成功正文缓存七天。"""
import re
from typing import Any

import requests

from data.cache import CacheManager
from utils.paths import project_state_dir

CONTENT_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"


def extract_art_code(url: str) -> str | None:
    match = re.search(r"(AN\d{10,})", str(url or ""))
    return match[1] if match else None


def _content(art_code: str) -> str:
    response = requests.get(CONTENT_URL, params={
        "art_code": art_code, "client_source": "web", "page_index": 1,
    }, timeout=8)
    response.raise_for_status()
    payload: Any = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    content = data.get("notice_content") if isinstance(data, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("公告正文为空")
    return re.sub(r"\s+", " ", content).strip()


def fetch_notice_content(art_code: str, *, cache: CacheManager | None = None) -> str:
    """按公告标识持久缓存成功正文，失败不写入缓存。"""
    store = cache if cache is not None else CacheManager(project_state_dir() / "cache.db")
    cached = store.get("a_announcement_content", art_code, "v1")
    if cached:
        return cached
    content = _content(art_code)
    store.put("a_announcement_content", art_code, "v1", content, ttl_seconds=7 * 86400)
    return content

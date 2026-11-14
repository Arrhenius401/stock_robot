"""同花顺主营资料单请求适配器；不将工商经营范围用于主营介绍。"""
import json
import logging
import re
from datetime import datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

from data.cache import CacheManager

logger = logging.getLogger(__name__)
TTL_SECONDS = 7 * 86400


def parse_profile(page: str, symbol: str, source: str) -> dict[str, Any]:
    soup = BeautifulSoup(page, "lxml")
    info = soup.find("ul", class_="main_intro_list")
    if info is None:
        raise ValueError("未找到主营介绍")
    # 页面中的证券代码必须与请求一致，禁止仅凭URL接受错标的页面。
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if not re.search(rf"[（(]\s*{re.escape(symbol)}\s*[)）]", title):
        raise ValueError("主营页面缺少证券代码身份")
    fields = {}
    for li in info.find_all("li"):
        label, sep, value = li.get_text("", strip=True).partition("：")
        if sep and label in {"主营业务", "产品名称", "产品类型"}:
            fields[label] = value.strip()
    main = fields.get("主营业务", "")
    if not main or len(main) > 2000:
        raise ValueError("主营内容缺失或异常")
    return {"symbol": symbol, "status": "ok", "main_business": main,
            "products": fields.get("产品名称", "")[:3000], "source": source,
            "fetched_at": datetime.now().astimezone().isoformat()}


def load_business_profile(cache: CacheManager, symbol: str, *, refresh: bool = False) -> dict[str, Any]:
    unavailable = {"symbol": symbol, "status": "unavailable", "main_business": ""}
    if not re.fullmatch(r"\d{6}", symbol):
        return unavailable
    source = f"https://basic.10jqka.com.cn/new/{symbol}/operate.html"
    if not refresh:
        cached = cache.get("business_profile", symbol, "latest")
        if cached:
            try:
                profile = json.loads(cached)
                if (isinstance(profile, dict) and profile.get("symbol") == symbol
                        and profile.get("source") == source and profile.get("main_business")
                        and profile.get("status") == "ok" and profile.get("fetched_at")):
                    return profile
            except (ValueError, TypeError):
                logger.debug("主营资料缓存损坏，重新查询 %s", symbol)
    try:
        response = requests.get(source, timeout=8, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/109.0.0.0 Safari/537.36"})
        response.raise_for_status()
        response.encoding = "gb2312"
        profile = parse_profile(response.text, symbol, source)
        cache.put("business_profile", symbol, "latest", json.dumps(profile, ensure_ascii=False), ttl_seconds=TTL_SECONDS)
        return profile
    except Exception as exc:  # noqa: BLE001 — 第三方页面及网络边界，失败不阻断报告
        logger.warning("主营资料获取失败 %s: %s", symbol, type(exc).__name__)
        return unavailable

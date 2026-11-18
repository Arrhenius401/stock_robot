"""个股主营资料单请求适配器；不将工商经营范围用于主营介绍。"""
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
F10_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
HK_PROFILE_REPORT = "RPT_HKF10_INFO_ORGPROFILE"
US_PROFILE_REPORT = "RPT_USF10_INFO_ORGPROFILE"
_F10_LABEL = "东财 F10 公司资料"
_THS_LABEL = "同花顺主营介绍"


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


def _clean_text(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def parse_f10_profile(rows: list[dict[str, Any]], symbol: str, market: str, source: str) -> dict[str, Any]:
    if market not in {"hk", "us"}:
        raise ValueError("不支持的 F10 主营市场")
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("F10 公司资料行数异常")
    row = rows[0]
    identity = "SECUCODE" if market == "hk" else "SECURITY_CODE"
    if row.get(identity) != symbol or not _clean_text(row.get("ORG_NAME")):
        raise ValueError("F10 公司资料证券身份不符或缺少公司名称")
    main = _clean_text(row.get("MAIN_BUSINESS")) if market == "hk" else ""
    main = main or _clean_text(row.get("ORG_PROFILE"))
    if not main or len(main) > 2000:
        raise ValueError("主营内容缺失或异常")
    return {"symbol": symbol, "market": market, "status": "ok", "main_business": main,
            "source": source, "source_label": _F10_LABEL, "reason": "",
            "fetched_at": datetime.now().astimezone().isoformat()}


def _fetch_f10_profile(symbol: str, market: str) -> dict[str, Any]:
    hk = market == "hk"
    identity = "SECUCODE" if hk else "SECURITY_CODE"
    response = requests.get(F10_URL, params={
        "reportName": HK_PROFILE_REPORT if hk else US_PROFILE_REPORT,
        "columns": "ALL", "filter": f'({identity}="{symbol}")',
        "pageNumber": 1, "pageSize": 20, "client": "PC",
        "source": "F10" if hk else "SECURITIES",
    }, timeout=8)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("F10 公司资料响应失败")
    result = payload.get("result")
    rows = result.get("data") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise TypeError("F10 公司资料缺失")
    return parse_f10_profile(rows, symbol, market, F10_URL)


def load_business_profile(cache: CacheManager, symbol: str, market: str = "a-shares", *,
                          refresh: bool = False) -> dict[str, Any]:
    is_a = market == "a-shares"
    label = _THS_LABEL if is_a else _F10_LABEL
    unavailable = {"symbol": symbol, "market": market, "status": "unavailable",
                   "main_business": "", "source": "", "source_label": label,
                   "fetched_at": None, "reason": "证券代码或市场无效"}
    patterns = {"a-shares": r"\d{6}", "hk": r"\d{5}\.HK", "us": r"[A-Z]{1,6}(?:[._-][A-Z]{1,2})?"}
    if market not in patterns or not re.fullmatch(patterns[market], symbol):
        return unavailable
    source = f"https://basic.10jqka.com.cn/new/{symbol}/operate.html" if is_a else F10_URL
    cache_key = f"{market}::{symbol}"
    if not refresh:
        cached = cache.get("business_profile", cache_key, "latest")
        if is_a and not cached:
            cached = cache.get("business_profile", symbol, "latest")
        if cached:
            try:
                profile = json.loads(cached)
                if (isinstance(profile, dict) and profile.get("symbol") == symbol
                        and profile.get("market", "a-shares") == market
                        and profile.get("source") == source and profile.get("main_business")
                        and profile.get("status") == "ok" and profile.get("fetched_at")):
                    return {**profile, "market": market, "source_label": label, "reason": ""}
            except (ValueError, TypeError):
                logger.debug("主营资料缓存损坏，重新查询 %s", symbol)
    try:
        if is_a:
            response = requests.get(source, timeout=8, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/109.0.0.0 Safari/537.36"})
            response.raise_for_status()
            response.encoding = "gb2312"
            profile = {**parse_profile(response.text, symbol, source),
                       "market": market, "source_label": label, "reason": ""}
        else:
            profile = _fetch_f10_profile(symbol, market)
        cache.put("business_profile", cache_key, "latest", json.dumps(profile, ensure_ascii=False), ttl_seconds=TTL_SECONDS)
        return profile
    except Exception as exc:  # noqa: BLE001 — 第三方页面及网络边界，失败不阻断报告
        logger.warning("主营资料获取失败 %s %s: %s", market, symbol, type(exc).__name__)
        reason = str(exc) if isinstance(exc, (ValueError, TypeError)) else "主营资料采集失败"
        return {**unavailable, "reason": reason}

"""东财美股 F10 行业采集；未核实的行情字段不参与经营指标派生。"""
from __future__ import annotations

import json
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

import requests

from data.cache import CacheManager
from data.collection_state import ValueResult, make_record
from data.schemas import IndustryData

logger = logging.getLogger(__name__)
PROFILE_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
SOURCE = "东财美股 F10 行业（BELONG_INDUSTRY）"
# 仅代码审查可登记协议；条目须含端点、fltt、字段映射及真实双缩放样本的SHA256。
# 2026-11-16 官方vendor映射及AAPL/BRK_B双缩放探针已核实单标的协议。
# 批量仍未证实；营收不在本次审核范围，禁止运行时配置boolean绕过。
_REVIEWED_PROTOCOLS: tuple[tuple[str, int, str, str], ...] = (
    ("stock/get", 2, "f116,f164,f167", "636412b0372ba9c2a773f5f44176f4373de52e71af895d1687e5e5e63c8cc14a"),
)
# 公开网页客户端标识，不是账户凭据。
_QUOTE_CLIENT = {"ut": "fa5fd1943c7b386f172d6893dbfba10b", "invt": 2}
_QUOTE_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}


def _basis_verified(endpoint: str, fields: str) -> bool:
    return any(path == endpoint and fltt == 2 and mapping == fields
               and re.fullmatch(r"[0-9a-f]{64}", evidence) is not None
               for path, fltt, mapping, evidence in _REVIEWED_PROTOCOLS)


def _object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TypeError("行情响应根必须为对象")
    return payload



def _code(symbol: str) -> str:
    """只转换已核实的东财类别股别名。"""
    return "BRK_B" if symbol == "BRK.B" else symbol


def _profiles(expression: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        response = requests.get(PROFILE_URL, params={
            "reportName": "RPT_USF10_INFO_ORGPROFILE",
            "columns": "SECUCODE,SECURITY_CODE,ORG_NAME,BELONG_INDUSTRY",
            "filter": expression, "source": "SECURITIES", "client": "PC",
            "pageSize": 500, "pageNumber": page,
        }, timeout=10)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("F10响应不是对象")
        result = payload.get("result")
        if payload.get("success") is not True or not isinstance(result, dict):
            raise ValueError("F10 行业响应失败或为空")
        data = result.get("data")
        pages = result.get("pages")
        if (not isinstance(data, list) or not all(isinstance(row, dict) for row in data)
                or type(pages) is not int or not 1 <= pages <= 100):
            raise ValueError("F10 行业分页结构无效")
        rows.extend(data)
        if page >= pages:
            return rows
        page += 1


def filter_members(rows: list[dict[str, Any]], industry: str, target: str) -> dict[str, str]:
    """仅保留同分类、名称完整、上市后缀有效的纯字母同行代码。"""
    members: dict[str, str] = {}
    for row in rows:
        code, secucode = row.get("SECURITY_CODE"), row.get("SECUCODE")
        name = row.get("ORG_NAME")
        if (not isinstance(code, str) or re.fullmatch(r"[A-Z]+", code) is None
                or code == _code(target) or not isinstance(name, str) or not name.strip()
                or row.get("BELONG_INDUSTRY") != industry
                or secucode not in {f"{code}.O", f"{code}.N", f"{code}.A"}):
            continue
        members[code] = name.strip()
    return members


def _positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _single_quote(secid: str) -> dict[str, Any]:
    response = requests.get("https://push2.eastmoney.com/api/qt/stock/get", params={
        "secid": secid, "fields": "f57,f58,f116,f164,f167,f127", "fltt": 2, **_QUOTE_CLIENT,
    }, timeout=10, headers=_QUOTE_HEADERS)
    response.raise_for_status()
    data = _object(response.json()).get("data")
    if not isinstance(data, dict) or data.get("f57") != secid.split(".", 1)[1]:
        raise ValueError("同行单标的行情身份无效")
    return {"symbol": data["f57"], "name": data.get("f58"),
            "market_cap": _positive(data.get("f116")), "pe": _positive(data.get("f164")),
            "pb": _positive(data.get("f167")), "sector": data.get("f127") or "",
            "basis_verified": _basis_verified("stock/get", "f116,f164,f167"), "revenue": None, "revenue_verified": False, "source": "东财美股单标的行情"}


def _quotes(secids: list[str]) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    quotes: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, str]] = []
    for offset in range(0, len(secids), 50):
        batch = secids[offset:offset + 50]
        try:
            response = requests.get("https://push2.eastmoney.com/api/qt/ulist.np/get", params={
                "secids": ",".join(batch), "fields": "f12,f14,f20,f9,f23,f100", "fltt": 2, **_QUOTE_CLIENT,
            }, timeout=10, headers=_QUOTE_HEADERS)
            response.raise_for_status()
            data = _object(response.json()).get("data")
            rows = data.get("diff") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise TypeError("批量行情无有效列表")
            expected = {item.split(".", 1)[1] for item in batch}
            if any(not isinstance(row, dict) or row.get("f12") not in expected for row in rows):
                raise ValueError("批量行情身份不匹配")
            for row in rows:
                quotes[row["f12"]] = {"symbol": row["f12"], "name": row.get("f14"),
                    "market_cap": _positive(row.get("f20")), "pe": _positive(row.get("f9")),
                    "pb": _positive(row.get("f23")), "sector": row.get("f100") or "",
                    "basis_verified": _basis_verified("ulist.np/get", "f20,f9,f23"), "revenue": None, "revenue_verified": False, "source": "东财美股批量行情"}
            attempts.append({"source": "东财美股批量行情", "status": "ok"})
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            logger.warning("美股同行批量行情失败: %s", exc)
            attempts.append({"source": "东财美股批量行情", "status": "failed", "reason": str(exc)})
        missing = [item for item in batch if not quotes.get(item.split(".", 1)[1], {}).get("basis_verified")
                   or any(_positive(quotes.get(item.split(".", 1)[1], {}).get(key)) is None
                          for key in ("market_cap", "pe", "pb"))]

        def fetch(secid: str) -> tuple[str, dict[str, Any] | None, str]:
            try:
                return secid, _single_quote(secid), ""
            except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
                logger.warning("美股同行行情失败 %s: %s", secid, exc)
                return secid, None, str(exc)

        with ThreadPoolExecutor(max_workers=5) as executor:
            for secid, quote, error in executor.map(fetch, missing):
                attempts.append({"source": secid, "status": "ok" if quote else "failed", "reason": error})
                if quote:
                    quotes[quote["symbol"]] = quote
    return quotes, attempts


def us_industry_result(symbol: str, *, cache: CacheManager | None = None, refresh: bool = False) -> ValueResult[IndustryData]:
    """已审核行情可用于比较；营收及经营评分保持独立门禁。"""
    stamp = datetime.now().astimezone()
    collected_at = stamp
    cache_status = "live"
    try:
        if re.fullmatch(r"[A-Z]+(?:\.[A-Z])?", symbol) is None:
            raise ValueError("美股代码格式无效")
        ticker = _code(symbol)
        def profiles_for(expression: str) -> list[dict[str, Any]]:
            key = f"us_industry::v1::{symbol}::{expression}"
            saved = cache.get("us_industry_profile", key, stamp.date().isoformat()) if cache and not refresh else None
            if saved:
                cached = json.loads(saved)
                if not isinstance(cached, list) or not all(isinstance(row, dict) for row in cached):
                    raise TypeError("行业缓存结构无效")
                return cached
            rows = _profiles(expression)
            if cache:
                cache.put("us_industry_profile", key, stamp.date().isoformat(), json.dumps(rows), ttl_seconds=86400)
            return rows

        profiles = profiles_for(f'(SECURITY_CODE="{ticker}")')
        if len(profiles) != 1:
            raise ValueError("目标 F10 身份不唯一")
        profile = profiles[0]
        industry = profile.get("BELONG_INDUSTRY")
        if (profile.get("SECURITY_CODE") != ticker
                or profile.get("SECUCODE") not in {f"{ticker}.O", f"{ticker}.N", f"{ticker}.A"}
                or not isinstance(profile.get("ORG_NAME"), str)
                or not profile["ORG_NAME"].strip()
                or not isinstance(industry, str) or not industry.strip()):
            raise ValueError("目标 F10 身份或行业无效")
        rows = profiles_for(f'(BELONG_INDUSTRY="{industry}")')
        members = filter_members(rows, industry, symbol)
        market = {"O": "105", "N": "106", "A": "107"}
        securities = {row["SECURITY_CODE"]: market[row["SECUCODE"].rsplit(".", 1)[1]] + "." + row["SECURITY_CODE"]
                      for row in [profile, *rows] if row.get("SECURITY_CODE") in {*members, ticker}
                      and isinstance(row.get("SECUCODE"), str) and row["SECUCODE"].rsplit(".", 1)[-1] in market}
        quote_key = f"us_industry::v2::{symbol}::{industry}"
        saved = cache.get("us_industry_quotes", quote_key, "snapshot") if cache and not refresh else None
        if saved:
            cached = _object(json.loads(saved))
            quotes, attempts = cached.get("quotes"), cached.get("attempts")
            if (cached.get("version") != 2 or not isinstance(quotes, dict)
                    or not all(isinstance(value, dict) for value in quotes.values())
                    or not isinstance(attempts, list) or not all(isinstance(value, dict) for value in attempts)):
                raise TypeError("行情缓存结构无效")
            collected_at = datetime.fromisoformat(cached["collected_at"])
            if collected_at.tzinfo is None or collected_at > stamp:
                raise ValueError("行情缓存采集时间无效")
            cache_status = "cache"
        else:
            quotes, attempts = _quotes(list(securities.values()))
            if cache and quotes:
                cache.put("us_industry_quotes", quote_key, "snapshot", json.dumps({"version": 2, "quotes": quotes, "attempts": attempts, "collected_at": stamp.isoformat()}), ttl_seconds=600)
        target = quotes.get(ticker, {})
        if not isinstance(target, dict) or target.get("basis_verified") is not True:
            target = {}
        valid = {code: quote for code, quote in quotes.items() if code in members
                 and isinstance(quote, dict) and quote.get("basis_verified") is True
                 and all(_positive(quote.get(key)) is not None for key in ("market_cap", "pe", "pb"))}

        reason = "TTM 营收字段未证实，不派生净利率，行业经营分不适用"
        if not target or len(valid) < 5:
            reason += "；已核实行情的目标或有效同行不足"
        value = IndustryData(
            symbol=symbol, industry=industry, sector=target.get("sector", ""), peers=list(valid),
            peer_names=members, peer_scope=SOURCE, peer_industry=industry,
            comparison_ready=len(valid) >= 5,
            comparison_snapshot={
                "source": SOURCE, "as_of": collected_at.isoformat(), "basis": "TTM",
                "candidate_count": len(members), "revenue": None, "revenue_verified": False,
                "target": target, "quotes": valid,
                "errors": [reason], "attempts": [*attempts, {"source": SOURCE, "status": "ok"},
                    {"source": "东财美股营收口径", "status": "unverified", "reason": reason}],
            },
        )
        value._target_mcap = target.get("market_cap")
        value._target_pe = target.get("pe")
        value._target_pb = target.get("pb")
        value._data_source = SOURCE
        return ValueResult(value, make_record(
            SOURCE, "partial", collected_at=collected_at, cache_status=cache_status, error_kind="unverified_basis", error_message=reason,
        ), [reason])
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        logger.warning("美股行业采集失败 %s: %s", symbol, exc)
        return ValueResult(None, make_record(
            SOURCE, "failed", collected_at=stamp, error_kind=type(exc).__name__, error_message=str(exc),
        ), [str(exc)])

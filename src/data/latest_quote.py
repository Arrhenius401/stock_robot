"""A股最新报价：腾讯→新浪→东方财富，独立于技术分析日线。"""
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import requests

from data.peer_comparison_fetch import request_with_deadline
from utils.numbers import is_finite_number, is_positive_finite

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")


def _exchange(symbol: str) -> str:
    if not re.fullmatch(r"[036489]\d{5}", symbol):
        raise ValueError("不支持的A股代码")
    return "sh" if symbol.startswith("6") else "sz" if symbol.startswith(("0", "3")) else "bj"


def _quote(symbol: str, source: str, price: Any, previous: Any, stamp: datetime) -> dict[str, Any]:
    if isinstance(price, bool):
        raise TypeError("布尔值不是报价")
    price = float(price)
    if not is_positive_finite(price) or stamp.tzinfo is None:
        raise ValueError("报价或行情时点无效")
    try:
        previous = None if isinstance(previous, bool) else float(previous)
    except (ValueError, TypeError):
        previous = None
    change = round((price / previous - 1) * 100, 2) if is_positive_finite(previous) else None
    if change is not None and not is_finite_number(change):
        change = None
    return {"status": "ok", "symbol": symbol, "price": price, "source": source,
            "quoted_at": stamp.isoformat(), "change_pct": change}


def parse_tencent(text: str, symbol: str) -> dict[str, Any]:
    exchange = _exchange(symbol)
    match = re.search(rf'v_{exchange}{symbol}="([^"\r\n]*)"', text)
    fields = match[1].split("~") if match else []
    if len(fields) <= 30 or fields[2] != symbol or not re.fullmatch(r"\d{14}", fields[30]):
        raise ValueError("腾讯报价身份或时间字段无效")
    s = fields[30]
    stamp = datetime.fromisoformat(f"{s[:4]}-{s[4:6]}-{s[6:8]}T{s[8:10]}:{s[10:12]}:{s[12:14]}+08:00")
    return _quote(symbol, "腾讯行情", fields[3], fields[4], stamp)


def parse_sina(text: str, symbol: str) -> dict[str, Any]:
    exchange = _exchange(symbol)
    match = re.search(rf'hq_str_{exchange}{symbol}="([^"\r\n]*)"', text)
    fields = match[1].split(",") if match else []
    if len(fields) <= 31:
        raise ValueError("新浪报价身份或字段无效")
    stamp = datetime.fromisoformat(f"{fields[30]}T{fields[31]}+08:00")
    return _quote(symbol, "新浪行情", fields[3], fields[2], stamp)


def parse_eastmoney(payload: dict, symbol: str) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("f57") != symbol:
        raise ValueError("东方财富报价身份无效")
    stamp = datetime.fromtimestamp(float(data["f86"]), tz=SHANGHAI)
    return _quote(symbol, "东方财富行情", data["f43"], data.get("f60"), stamp)


def load_latest_quote(symbol: str, *, daily_end: str | None = None,
                      now: datetime | None = None) -> dict[str, Any]:
    """每次新报告最多12秒，不读取估值或旧报价缓存；慢请求迟到不回写。"""
    now = now or datetime.now(SHANGHAI)
    deadline = time.monotonic() + 12
    attempts: list[dict[str, str]] = []
    try:
        exchange = _exchange(symbol)
    except ValueError:
        return {"status": "unavailable", "symbol": symbol, "attempts": [
            {"source": "证券身份校验", "status": "failed", "reason": "不支持的A股代码"}],
            "fetched_at": datetime.now(SHANGHAI).isoformat()}
    jobs = [
        ("腾讯行情", f"https://qt.gtimg.cn/q={exchange}{symbol}", parse_tencent),
        ("新浪行情", f"https://hq.sinajs.cn/list={exchange}{symbol}", parse_sina),
        ("东方财富行情", ("https://push2.eastmoney.com/api/qt/stock/get?"
         f"secid={'1' if exchange == 'sh' else '0'}.{symbol}&fields=f43,f57,f58,f60,f86&fltt=2"), parse_eastmoney),
    ]
    for source, url, parser in jobs:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        request_deadline = min(deadline, time.monotonic() + 4)
        timeout = min(4, remaining)
        try:
            response = request_with_deadline(lambda quote_url=url, quote_timeout=timeout: requests.get(
                quote_url, timeout=quote_timeout, headers={"Referer": "https://finance.sina.com.cn",
                    "User-Agent": "Mozilla/5.0"}), request_deadline)
            response.raise_for_status()
            response.encoding = "gbk" if source != "东方财富行情" else "utf-8"
            data = response.json() if source == "东方财富行情" else response.text
            quote = parser(data, symbol)
            stamp = datetime.fromisoformat(quote["quoted_at"])
            if (stamp > now + timedelta(minutes=2) or stamp < now - timedelta(days=14)
                    or (daily_end and stamp.date().isoformat() < daily_end)):
                raise ValueError("行情时点过旧、早于日线或位于未来")
            if time.monotonic() > deadline:
                raise TimeoutError("报价超过总预算")
            attempts.append({"source": source, "status": "ok"})
            return {**quote, "fetched_at": datetime.now(SHANGHAI).isoformat(), "attempts": attempts}
        except Exception as exc:  # noqa: BLE001 — 网络/第三方响应隔离，单源失败切备用
            logger.warning("最新报价源失败 %s %s: %s", symbol, source, type(exc).__name__)
            attempts.append({"source": source, "status": "failed", "reason": str(exc)[:160]})
    return {"status": "unavailable", "symbol": symbol, "attempts": attempts,
            "fetched_at": datetime.now(SHANGHAI).isoformat()}


def quote_is_today(quote: dict, zone: ZoneInfo = SHANGHAI) -> bool:
    """报价时点是否为当地今天；休市日或过期快照不算盘中报价。"""
    stamp = str(quote.get("quoted_at") or "")
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    return parsed.astimezone(zone).date() == datetime.now(zone).date()


def load_hk_quote(symbol: str, *, now: datetime | None = None, daily_end: str | None = None) -> dict[str, Any]:
    """港股快照：允许最近交易日报价，由共用入口判断市场状态。"""
    from data.hk_alternatives import tencent_snapshot

    code = symbol.removesuffix(".HK")
    if not re.fullmatch(r"\d{5}", code):
        return {"status": "unavailable", "symbol": symbol,
                "attempts": [{"source": "港股报价身份校验", "status": "failed",
                              "reason": "港股代码必须为五位数字"}]}
    try:
        snapshot = tencent_snapshot(code)
        price = snapshot.get("price")
        quoted = snapshot.get("quoted_at")
        if (not is_positive_finite(price) or not isinstance(quoted, datetime)
                or quoted.tzinfo is None):
            raise ValueError("腾讯港股报价缺少有效价格或时点")
        current = now or datetime.now(HONG_KONG)
        current = current.replace(tzinfo=HONG_KONG) if current.tzinfo is None else current.astimezone(HONG_KONG)
        if (quoted > current + timedelta(minutes=2) or quoted < current - timedelta(days=14)
                or (daily_end and quoted.astimezone(HONG_KONG).date().isoformat() < daily_end)):
            raise ValueError("港股行情时点过旧、早于日线或位于未来")
        change_value = snapshot.get("change_pct")
        change = float(cast(float, change_value)) if is_finite_number(change_value) else None
        return {"status": "ok", "symbol": symbol, "name": snapshot.get("name", ""),
                "price": float(cast(float, price)), "quoted_at": quoted.astimezone(HONG_KONG).isoformat(),
                "source": "腾讯港股快照（延迟）", "change_pct": change,
                "note": f"行情时间：{quoted.astimezone(HONG_KONG):%Y-%m-%d %H:%M:%S}"}
    except Exception as exc:  # noqa: BLE001 — 第三方快照边界，失败回退收盘价
        logger.warning("港股最新报价失败 %s: %s", symbol, type(exc).__name__)
        return {"status": "unavailable", "symbol": symbol,
                "attempts": [{"source": "腾讯港股快照", "status": "failed",
                              "reason": str(exc)[:160]}]}


def price_presentation(quote: dict, latest_close: float | None, daily_end: str | None) -> dict:
    """所有输出端共享明确的报价或收盘回退标签。"""
    status = str(quote.get("status") or "unavailable")
    if status == "ok" and is_positive_finite(quote.get("price")):
        stamp = str(quote.get("quoted_at", ""))
        try:
            parsed = datetime.fromisoformat(stamp)
            if parsed.tzinfo is None:
                raise ValueError("行情时间缺少时区")
        except ValueError:
            return price_presentation({"status": "unavailable", "note": "行情时间无效，回退日线收盘价"},
                                      latest_close, daily_end)
        source = str(quote.get("source", "来源未记录"))
        offset = parsed.strftime("%z")
        zone_label = f"UTC{int(offset[:3]):+d}" + (f":{offset[3:]}" if offset[3:] != "00" else "") if offset else "时区未记录"
        state = str(quote.get("market_state") or "交易中")
        note = f"{state}；{parsed:%Y-%m-%d %H:%M} {zone_label}"
        return {"value": quote["price"], "kind": "quote", "as_of": stamp, "source": source,
                "note": note, "change_pct": quote.get("change_pct")}
    if status in {"market_closed", "market_pre"} and latest_close is not None:
        prefix = "已收盘" if status == "market_closed" else "尚未开盘"
        return {"value": latest_close, "kind": "close_fallback", "as_of": daily_end,
                "source": "日线收盘价",
                "note": f"{prefix}，日线收盘价；截至 {daily_end or '未记录'}",
                "change_pct": None}
    fallback = str(quote.get("note") or "报价暂不可用，回退日线收盘价")
    return {"value": latest_close, "kind": "close_fallback", "as_of": daily_end, "source": "日线收盘价",
            "note": f"{fallback}；截至 {daily_end or '未记录'}" if latest_close is not None
                    else "最新报价与收盘价均不可用", "change_pct": None}

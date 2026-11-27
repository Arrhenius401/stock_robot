"""三市场个股股息率：优先使用可直接核验的公开字段，并保留来源与口径。"""
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import akshare as ak
import requests
from pandas import isna

from data.hk_eastmoney import finite_number

logger = logging.getLogger(__name__)
A_SOURCE = "东方财富分红送配"
HK_SOURCE = "东方财富港股F10"
US_SOURCE = "腾讯美股快照"


@dataclass(frozen=True)
class DividendYield:
    value: float | None
    as_of: date | None
    source: str
    basis: str
    reason: str = ""


def _date(value: Any) -> date | None:
    if isna(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def a_share_dividend_yield(symbol: str) -> DividendYield:
    """最近已实施现金分红方案的股息率；东财字段为小数比例，展示前转百分比。"""
    try:
        frame: Any = ak.stock_fhps_detail_em(symbol=symbol)
        rows = frame.to_dict("records")
    except Exception as error:  # noqa: BLE001 — 第三方分红接口边界，缺失不阻断财务
        logger.warning("A 股股息率获取失败 %s: %s", symbol, type(error).__name__)
        return DividendYield(None, None, A_SOURCE, "", "东财分红送配暂不可用")
    candidates: list[tuple[date, float]] = []
    for row in rows:
        if str(row.get("方案进度") or "").strip() != "实施分配":
            continue
        value = finite_number(row.get("现金分红-股息率"))
        day = _date(row.get("股权登记日")) or _date(row.get("除权除息日")) or _date(row.get("最新公告日期"))
        if value is None or value <= 0 or day is None:
            continue
        candidates.append((day, value))
    if not candidates:
        return DividendYield(None, None, A_SOURCE, "", "没有已实施的现金分红股息率")
    day, raw = max(candidates, key=lambda item: item[0])
    return DividendYield(
        raw * 100, day, A_SOURCE,
        "最近已实施现金分红方案股息率（东财，方案口径）",
    )


def hk_dividend_yield(code: str) -> DividendYield:
    """东财港股 F10 报告期股息率；字段单位已为百分比。"""
    try:
        response = requests.get(
            "https://datacenter.eastmoney.com/securities/api/data/v1/get",
            params={
                "reportName": "RPT_HKF10_FN_MAININDICATOR",
                "columns": "SECUCODE,SECURITY_CODE,REPORT_DATE,DIVIDEND_RATE",
                "filter": f'(SECUCODE="{code}.HK")',
                "pageNumber": 1,
                "pageSize": 20,
                "sortColumns": "REPORT_DATE",
                "sortTypes": -1,
                "source": "F10",
                "client": "PC",
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) and payload.get("success") is True else None
        rows = result.get("data") if isinstance(result, dict) else None
        if not isinstance(rows, list):
            raise TypeError("港股 F10 股息率响应失败")
    except (requests.RequestException, ValueError, TypeError) as error:
        logger.warning("港股股息率获取失败 %s: %s", code, type(error).__name__)
        return DividendYield(None, None, HK_SOURCE, "", "东财港股F10股息率暂不可用")
    candidates: list[tuple[date, float]] = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("SECUCODE") or "") != f"{code}.HK":
            continue
        value = finite_number(row.get("DIVIDEND_RATE"))
        day = _date(row.get("REPORT_DATE"))
        if value is None or value < 0 or day is None:
            continue
        candidates.append((day, value))
    if not candidates:
        return DividendYield(None, None, HK_SOURCE, "", "港股F10未提供有效股息率")
    day, value = max(candidates, key=lambda item: item[0])
    return DividendYield(value, day, HK_SOURCE, "东财F10报告期股息率（口径未声明）")


def us_dividend_yield(ticker: str) -> DividendYield:
    """腾讯美股快照第 52 位为股息率百分比；校验代码、币种与行情时间。"""
    # 2026-11-24 实测 AAPL/MSFT/JPM/KO/XOM/PG 的字段 52 与公开股息率一致；
    # 这是未文档化第三方字段，口径未声明，报告必须保留来源与日期。
    try:
        response = requests.get(f"https://qt.gtimg.cn/q=us{ticker}", timeout=10)
        response.raise_for_status()
        text = response.content.decode("gbk", errors="replace")
        matched = re.search(rf'v_us{re.escape(ticker)}="([^"]*)"', text, re.IGNORECASE)
        if matched is None:
            raise ValueError("腾讯美股快照代码不匹配")
        fields = matched.group(1).split("~")
        if len(fields) < 53 or fields[35] != "USD":
            raise ValueError("腾讯美股快照字段不足或币种不是 USD")
        expected = ticker.upper().replace("_", ".").split(".")[0]
        actual = fields[2].upper().replace("_", ".").split(".")[0]
        if actual != expected:
            raise ValueError("腾讯美股快照证券身份不匹配")
        quoted_at = _date(fields[30][:10])
        value = finite_number(fields[52])
        if quoted_at is None or value is None or value < 0:
            raise ValueError("腾讯美股快照股息率或行情日期无效")
    except (requests.RequestException, ValueError, TypeError, IndexError) as error:
        logger.warning("美股股息率获取失败 %s: %s", ticker, type(error).__name__)
        return DividendYield(None, None, US_SOURCE, "", "腾讯美股快照股息率暂不可用")
    return DividendYield(value, quoted_at, US_SOURCE, "腾讯美股快照股息率字段（口径未声明）")

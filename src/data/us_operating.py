"""美股F10经营数据：实际连续单季TTM，缺项不填零。"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any

import requests

from data.cache import CacheManager
from utils.numbers import is_finite_number

logger = logging.getLogger(__name__)
URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
GENERAL = "RPT_USF10_FN_GMAININDICATOR"
BANK = "RPT_USF10_FN_BMAININDICATOR"


def _date(value: Any) -> date:
    return date.fromisoformat(str(value)[:10])


def build_operating(rows: list[dict[str, Any]], code: str, source: str) -> dict[str, Any] | None:
    """仅消费明确单季；四季缺失不回退到主指标或旧TTM窗口。"""
    today = datetime.now().astimezone().date()
    singles = sorted([r for r in rows if r.get("DATE_TYPE") == "单季报"],
                     key=lambda r: str(r.get("REPORT_DATE")))[-8:]
    if len(singles) < 4 or any(r.get("SECURITY_CODE") != code for r in rows):
        return None
    ends = [_date(r.get("REPORT_DATE")) for r in singles]
    starts = [_date(r.get("START_DATE")) for r in singles]
    if (len(set(ends)) != len(ends) or ends[-1] > today
            or any(not 70 <= (end - start).days <= 112 for start, end in zip(starts, ends, strict=True))
            or any(starts[i] != ends[i-1] + timedelta(days=1) for i in range(1, len(singles)))):
        return None
    currency_key = "CURRENCY" if source == BANK else "CURRENCY_ABBR"
    currencies = {r.get(currency_key) for r in singles}
    if len(currencies) != 1 or not next(iter(currencies)):
        return None
    revenue_key = "TOTAL_INCOME" if source == BANK else "OPERATE_INCOME"
    recent = singles[-4:]
    revenue_values = [r.get(revenue_key) for r in recent]
    profit_values = [r.get("PARENT_HOLDER_NETPROFIT") for r in recent]
    if not all(is_finite_number(v) and not isinstance(v, bool) for v in revenue_values):
        return None
    revenue = sum(float(v) for v in revenue_values if v is not None)
    if revenue <= 0:
        return None
    profit = sum(float(v) for v in profit_values if v is not None) if all(is_finite_number(v) and not isinstance(v, bool) for v in profit_values) else None
    previous_values = [r.get(revenue_key) for r in singles[:4]]
    previous = sum(float(v) for v in previous_values if v is not None) if len(singles) == 8 and all(is_finite_number(v) and not isinstance(v, bool) for v in previous_values) else None
    return {"symbol": code, "start": starts[-4].isoformat(), "end": ends[-1].isoformat(),
            "basis": "TTM", "source": source, "currency": next(iter(currencies)),
            "notice_dates": [r.get("NOTICE_DATE") for r in recent],
            "quarters": [{"start": r.get("START_DATE"), "end": r.get("REPORT_DATE"),
                          "notice": r.get("NOTICE_DATE"), "currency": r.get(currency_key), "source": source} for r in singles],
            "revenue": revenue, "net_profit": profit, "previous_revenue": previous,
            "revenue_growth_yoy": (revenue / previous - 1) * 100 if previous is not None and previous > 0 else None,
            "net_margin": profit / revenue * 100 if profit is not None else None}


def fetch_operating(code: str) -> dict[str, Any] | None:
    if code in {"BRK.B", "BRK_B"}:
        return None
    for source in (GENERAL, BANK):
        response = requests.get(URL, params={"reportName": source, "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")', "pageNumber": 1, "pageSize": 500,
            "sortColumns": "REPORT_DATE", "sortTypes": "-1", "source": "SECURITIES", "client": "PC"}, timeout=10)
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result") or {}
        rows = result.get("data") or []
        if not rows and payload.get("message") == "返回数据为空":
            continue
        if payload.get("success") is not True or not isinstance(rows, list):
            raise ValueError("F10经营数据响应失败")
        return build_operating(rows, code, source)
    return None


def load_operating(codes: list[str], *, cache: CacheManager | None = None, refresh: bool = False) -> dict[str, Any]:
    """每代码每日缓存，空结果亦缓存None；请求失败隔离，刷新显式绕过。"""
    day = datetime.now().astimezone().date().isoformat()

    def load(code: str) -> tuple[str, Any]:
        key = f"us_operating::v1::{code}"
        try:
            saved = cache.get("us_operating", key, day) if cache and not refresh else None
            if saved is not None:
                return code, json.loads(saved)
            value = fetch_operating(code)
            if cache:
                cache.put("us_operating", key, day, json.dumps(value), ttl_seconds=86400)
            return code, value
        except (requests.RequestException, ValueError, TypeError, KeyError, OverflowError) as error:
            logger.warning("美股经营数据失败 %s: %s", code, error)
            return code, None

    with ThreadPoolExecutor(max_workers=4) as executor:
        return dict(executor.map(load, list(dict.fromkeys(codes))))

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


def _finite(value: Any) -> float | None:
    return float(value) if not isinstance(value, bool) and is_finite_number(value) else None


def _annual_row(rows: list[dict[str, Any]], code: str, source: str) -> dict[str, Any] | None:
    if source not in {GENERAL, BANK} or any(row.get("SECURITY_CODE") != code for row in rows):
        return None
    return max((row for row in rows if row.get("DATE_TYPE") == "年报"),
               key=lambda row: str(row.get("REPORT_DATE") or ""), default=None)


def _percent(numerator: float | None, denominator: float | None) -> float | None:
    return _finite(numerator / denominator * 100) if numerator is not None and denominator and denominator > 0 else None


def _ratio_pct(value: Any) -> float | None:
    ratio = _finite(value)
    return ratio * 100 if ratio is not None and 0 <= ratio <= 1 else None


def _annual_facts(row: dict[str, Any], source: str) -> dict[str, Any] | None:
    try:
        start, end = _date(row.get("START_DATE")), _date(row.get("REPORT_DATE"))
    except (TypeError, ValueError):
        return None
    if not 350 <= (end - start).days <= 380 or end > datetime.now().astimezone().date():
        return None
    bank = source == BANK
    currency = row.get("CURRENCY" if bank else "CURRENCY_ABBR")
    if not isinstance(currency, str) or not currency.strip():
        return None
    revenue = _finite(row.get("TOTAL_INCOME" if bank else "OPERATE_INCOME"))
    profit = _finite(row.get("PARENT_HOLDER_NETPROFIT"))
    return {
        "start": start.isoformat(), "end": end.isoformat(), "basis": "FY", "source": source,
        "currency": currency, "notice_dates": [row.get("NOTICE_DATE")],
        "revenue": revenue, "net_profit": profit,
        "revenue_growth_yoy": _finite(row.get("TOTAL_INCOME_YOY" if bank else "OPERATE_INCOME_YOY")),
        "net_margin": _percent(profit, revenue) if bank else _finite(row.get("NET_PROFIT_RATIO")),
        "roe": _finite(row.get("ROE" if bank else "ROE_AVG")),
    }


def build_annual(rows: list[dict[str, Any]], code: str, source: str) -> dict[str, Any] | None:
    """提取最近完整年度的 F10 披露数据。"""
    row = _annual_row(rows, code, source)
    return _annual_facts(row, source) if row is not None else None


def build_special(rows: list[dict[str, Any]], code: str, source: str) -> dict[str, Any] | None:
    """银行专项使用与年度收入一致的 F10 披露行。"""
    row = _annual_row(rows, code, source) if source == BANK else None
    annual = _annual_facts(row, source) if row is not None else None
    if row is None or annual is None:
        return None
    return _bank_metrics(row, annual["revenue"])


def _bank_metrics(row: dict[str, Any], revenue: float | None) -> dict[str, Any]:
    return {
        "net_interest_income_ratio_pct": _percent(_finite(row.get("NET_INTEREST_INCOME")), revenue),
        "provision_to_revenue_pct": _percent(_finite(row.get("LOAN_LOSS_PROVISION")), revenue),
        "loan_to_deposit_pct": _ratio_pct(row.get("LOAN_DEPOSIT")),
        "loan_to_assets_pct": _ratio_pct(row.get("LOAN_ASSETS")),
        "roe_pct": _finite(row.get("ROE")),
        "roa_pct": _finite(row.get("ROA")),
    }


def build_jpm_special(rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: dict[tuple[date, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("SECURITY_CODE") != "JPM" or row.get("SECUCODE") != "JPM.N" or row.get("CURRENCY") != "USD":
            continue
        basis = str(row.get("REPORT_TYPE")).rsplit("/", 1)[-1]
        if basis not in {"Q6", "Q9", "FY"} or row.get("DATE_TYPE") not in {"累计季报", "年报"}:
            continue
        start, end = _date(row.get("START_DATE")), _date(row.get("REPORT_DATE"))
        if start != date(end.year, 1, 1) or end > datetime.now().astimezone().date():
            continue
        candidates.setdefault((end, basis), []).append(row)
    if not candidates:
        return {}
    end, basis = max(candidates)
    rows_at_end = candidates[end, basis]
    if len(rows_at_end) != 1:
        return {}
    row = rows_at_end[0]
    return {**_bank_metrics(row, _finite(row.get("TOTAL_INCOME"))), "basis": basis,
            "start": _date(row["START_DATE"]).isoformat(), "end": end.isoformat(), "source": BANK, "currency": "USD"}


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
    currency = next(iter(currencies))
    annual = build_annual(rows, code, source)
    if annual is not None and annual["currency"] != currency:
        annual = None
    special = build_special(rows, code, source) if annual is not None else None
    return {"symbol": code, "start": starts[-4].isoformat(), "end": ends[-1].isoformat(),
            "basis": "TTM", "source": source, "currency": next(iter(currencies)),
            "notice_dates": [r.get("NOTICE_DATE") for r in recent],
            "quarters": [{"start": r.get("START_DATE"), "end": r.get("REPORT_DATE"),
                          "notice": r.get("NOTICE_DATE"), "currency": r.get(currency_key), "source": source} for r in singles],
            "revenue": revenue, "net_profit": profit, "previous_revenue": previous,
            "revenue_growth_yoy": (revenue / previous - 1) * 100 if previous is not None and previous > 0 else None,
            "net_margin": profit / revenue * 100 if profit is not None else None,
            "annual": annual, "special": special}


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
        operating = build_operating(rows, code, source)
        if code == "JPM" and source == BANK:
            special = build_jpm_special(rows)
            if special:
                return {**(operating or {}), "latest_bank_special": special}
        return operating
    return None


def load_operating(codes: list[str], *, cache: CacheManager | None = None, refresh: bool = False) -> dict[str, Any]:
    """每代码每日缓存，空结果亦缓存None；请求失败隔离，刷新显式绕过。"""
    day = datetime.now().astimezone().date().isoformat()

    def load(code: str) -> tuple[str, Any]:
        key = f"us_operating::v{3 if code == 'JPM' else 2}::{code}"
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

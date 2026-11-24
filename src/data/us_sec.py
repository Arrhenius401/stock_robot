"""SEC XBRL 营业收入事实；只服务 BRK.B 展示，不进入通用评分。"""
import json
import logging
import math
import time
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

import requests

from data.cache import CacheManager
from utils.paths import project_state_dir

logger = logging.getLogger(__name__)
SOURCE = "SEC XBRL"
BRK_CIK = "0001067983"
BRK_CONCEPT = "RevenueFromContractWithCustomerExcludingAssessedTax"
USER_AGENT = "stock-robot research local@example.com"


@dataclass(frozen=True)
class SecRevenueFact:
    start: date
    end: date
    value: float
    form: str
    filed: date
    concept: str
    currency: str
    basis: str
    cache_stale: bool = False


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def parse_company_concept(payload: dict[str, Any], concept: str) -> list[SecRevenueFact]:
    if payload.get("tag") != concept or payload.get("taxonomy") != "us-gaap":
        raise ValueError("SEC 响应概念或命名空间不匹配")
    units = payload.get("units") or {}
    rows = units.get("USD")
    if rows is None:
        raise ValueError("SEC 响应缺少 USD 单位数据")
    if not isinstance(rows, list):
        raise TypeError("SEC USD 单位数据类型异常")
    facts: dict[tuple[date, date], SecRevenueFact] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("form") not in {"10-Q", "10-K"}:
            continue
        start, end, filed = _date(row.get("start")), _date(row.get("end")), _date(row.get("filed"))
        value = row.get("val")
        if start is None or end is None or filed is None or start > end:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            continue
        fact = SecRevenueFact(start, end, float(value), str(row["form"]), filed,
                              concept, "USD", "直接披露")
        previous = facts.get((start, end))
        if previous is None or filed >= previous.filed:
            facts[(start, end)] = fact
    if not facts:
        raise ValueError("SEC 营业收入没有有效期间")
    return [facts[key] for key in sorted(facts)]


def single_quarters(facts: list[SecRevenueFact]) -> list[SecRevenueFact]:
    output: dict[date, SecRevenueFact] = {}
    for fact in sorted(facts, key=lambda item: (item.end, (item.end - item.start).days)):
        days = (fact.end - fact.start).days
        if 80 <= days <= 100:
            output[fact.end] = fact
            continue
        if 170 <= days <= 190:
            q1 = next((item for item in facts if item.start == fact.start and 80 <= (item.end - item.start).days <= 100), None)
            if q1 is not None and fact.value > q1.value:
                output[fact.end] = SecRevenueFact(
                    q1.end + timedelta(days=1), fact.end, fact.value - q1.value,
                    fact.form, fact.filed, fact.concept, fact.currency, "累计差额")
            continue
        if 260 <= days <= 280:
            q1 = next((item for item in facts if item.start == fact.start and 80 <= (item.end - item.start).days <= 100), None)
            h1 = next((item for item in facts if item.start == fact.start and 170 <= (item.end - item.start).days <= 190), None)
            if q1 is not None and h1 is not None and fact.value > h1.value:
                output[fact.end] = SecRevenueFact(
                    h1.end + timedelta(days=1), fact.end, fact.value - h1.value,
                    fact.form, fact.filed, fact.concept, fact.currency, "累计差额")
            continue
        if 350 <= days <= 380:
            q3 = next((item for item in facts if item.start == fact.start and 260 <= (item.end - item.start).days <= 280), None)
            if q3 is not None and fact.value > q3.value:
                output[fact.end] = SecRevenueFact(
                    q3.end + timedelta(days=1), fact.end, fact.value - q3.value,
                    fact.form, fact.filed, fact.concept, fact.currency, "累计差额")
    for fact in facts:
        if (80 <= (fact.end - fact.start).days <= 100 and fact.end in output
                and not math.isclose(output[fact.end].value, fact.value, rel_tol=1e-9, abs_tol=1)):
            raise ValueError(f"SEC 单季直接披露与累计差额冲突：{fact.end}")
    return [output[key] for key in sorted(output)]


def _source_cache() -> CacheManager:
    return CacheManager(project_state_dir() / "cache.db")


def load_brk_revenue(*, refresh: bool = False) -> list[SecRevenueFact]:
    """已与原始10-Q对账的概念；保留旧成功响应及其 filed/期间。"""
    cache = _source_cache()
    saved = cache.get_entry("sec_revenue", BRK_CIK, "success-v1")
    def parse(payload: dict[str, Any]) -> list[SecRevenueFact]:
        if payload.get("cik") != int(BRK_CIK):
            raise ValueError("SEC BRK.B 身份不匹配")
        return single_quarters(parse_company_concept(payload, BRK_CONCEPT))
    if saved and not refresh and time.time() - saved.created_at <= saved.ttl_seconds:
        return parse(json.loads(saved.value))
    url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{BRK_CIK}/us-gaap/{BRK_CONCEPT}.json"
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        response.raise_for_status()
        payload = response.json()
        facts = parse(payload)
        # 2026-11-24 已与 10-Q 0001193125-26-452412 的 USD inline XBRL 对账。
        cache.put("sec_revenue", BRK_CIK, "success-v1", json.dumps(payload), ttl_seconds=86400)
        return facts
    except (requests.RequestException, OSError, TypeError, ValueError) as error:
        logger.warning("SEC 收入刷新失败：%s", type(error).__name__)
        if saved is not None:
            return [replace(fact, cache_stale=True) for fact in parse(json.loads(saved.value))]
        raise

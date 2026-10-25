"""有预算的批量取数；所有缓存保存公开字段，不依赖 PrivateAttr。"""
import hashlib
import html
import json
import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from typing import Any

import requests

from data.cache import CacheManager
from data.industry_mapping_builder import _HEADERS
from data.peer_comparison_rules import finite, iso_date, normalize_financial_rows
from data.schemas import IndustryData, PeerBasicInfo

logger = logging.getLogger(__name__)
A_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HK_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
MAX_BATCH = 50
PAGE_SIZE = 500
COLLECTION_BUDGET = 60.0


class ComparisonFetchError(RuntimeError):
    """批量接口失败、预算耗尽或契约不完整。"""


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def read_snapshot(cache: CacheManager | None, category: str, key: str,
                  ttl: int) -> tuple[Any, bool]:
    """用显式时间验证新鲜度，保留 TTL 到期的成功快照供失败回退。"""
    if cache is None:
        return None, False
    raw = cache.get_stale(category, key, "v1")
    if not raw:
        return None, False
    try:
        envelope = json.loads(raw)
        age = time.time() - float(envelope["saved_at"])
        return envelope["payload"], 0 <= age < ttl
    except (ValueError, TypeError, KeyError):
        logger.warning("同行比较缓存格式无效：%s", category)
        return None, False


def write_snapshot(cache: CacheManager | None, category: str, key: str,
                   payload: Any, ttl: int) -> None:
    if cache is not None:
        cache.put(category, key, "v1", json.dumps({"saved_at": time.time(), "payload": payload},
                  ensure_ascii=False, allow_nan=False), ttl_seconds=ttl)


def request_with_deadline(request: Callable[[], Any], deadline: float) -> Any:
    """按总墙钟预算等待，底层慢滴流即使未触发空闲超时也不阻塞调用方。"""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ComparisonFetchError("同行采集预算已用尽")
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="peer-request")
    future = executor.submit(request)
    try:
        return future.result(timeout=remaining)
    except TimeoutError as exc:
        raise ComparisonFetchError("同行请求超过总采集预算，迟到结果未采用") from exc
    finally:
        future.cancel()
        # 网络线程只返回响应，不负责缓存或采集状态；迟到不能回写成功结果。
        executor.shutdown(wait=False, cancel_futures=True)


def _response(url: str, deadline: float, *, params: dict | None = None) -> requests.Response:
    remaining = max(0.001, deadline - time.monotonic())
    response = request_with_deadline(
        lambda: requests.get(url, params=params, headers=_HEADERS,
                             timeout=(min(3.0, remaining / 2), min(8.0, remaining / 2))), deadline)
    response.raise_for_status()
    if time.monotonic() >= deadline:
        raise ComparisonFetchError("同行请求超过采集预算，迟到结果未采用")
    return response


def parse_a_profile(text: str) -> dict[str, str]:
    """只接受个股页明确的 III 级链接，不遍历行业树或回填正式映射表。"""
    result = {}
    for href, label in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, re.DOTALL):
        code = re.search(r"industryCode=(\d{6}\.SI)(?:&|$)", html.unescape(href))
        plain = html.unescape(re.sub(r"<[^>]+>", "", label)).strip()
        if code and plain.startswith("III"):
            result.update(code=code[1], level3=plain[3:].strip())
        elif code and plain.startswith("II"):
            result["level2"] = plain[2:].strip()
        elif code and plain.startswith("I"):
            result["level1"] = plain[1:].strip()
    if not result.get("code") or not result.get("level3"):
        raise ComparisonFetchError("未取得精确申万三级分类")
    return result


def parse_a_members(text: str, industry_code: str) -> list[dict[str, Any]]:
    """保留所有明确成分，包括缺市值、ST；成分表三级列不冒充二级。"""
    rows: dict[str, dict[str, Any]] = {}
    for attrs, body in re.findall(r"<tr\b([^>]*)>(.*?)</tr>", text, re.DOTALL):
        code = re.search(r'data-stock-code=["\'](\d{6})\.(SH|SZ|BJ)["\']', attrs)
        if not code or f"industryCode={industry_code}" not in html.unescape(body):
            continue
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", body, re.DOTALL)
        if len(cells) < 3:
            continue
        name = html.unescape(re.sub(r"<[^>]+>", "", cells[2])).strip()
        cap = re.search(r'data-market-cap=["\']([^"\']+)', attrs)
        market_cap = finite(cap[1]) if cap else None
        rows[code[1]] = {"symbol": code[1], "name": name, "exchange": code[2],
                         "market_cap": finite(market_cap * 1e8) if market_cap is not None else None}
    if not rows:
        raise ComparisonFetchError("申万三级成分解析为空或行业证据不符")
    return list(rows.values())


def parse_tencent_quotes(text: str, requested: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for exchange, symbol, body in re.findall(r'v_(sh|sz|bj)(\d{6})="([^"]*)"', text):
        if requested.get(symbol, "").lower() != exchange:
            continue
        fields = body.split("~")
        if len(fields) < 47 or fields[2] != symbol:
            continue
        stamp = fields[30]
        as_of = iso_date(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}") if len(stamp) >= 8 else ""
        if not as_of or as_of > datetime.now().astimezone().date().isoformat():
            continue
        # 官网前端独立映射 52 为 dynamic_ratio、53 为 lyr_ratio；不以二者代替 39。
        rows.append({"symbol": symbol, "name": fields[1], "as_of": as_of,
                     "pe_ttm": finite(fields[39]), "pb": finite(fields[46])})
    return rows


def _parallel_batches(jobs: list, worker, deadline: float) -> tuple[list, list[str]]:
    """最多两个在途任务，预算到期不排队新请求，不等待迟到任务回写。"""
    output, errors = [], []
    iterator = iter(jobs)
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="peer-comparison")
    pending = {}
    try:
        for _ in range(2):
            job = next(iterator, None)
            if job is not None and time.monotonic() < deadline:
                pending[executor.submit(worker, job)] = job
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                errors.append("同行采集预算已用尽，部分批次未取得")
                break
            done, _ = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                errors.append("同行采集预算已用尽，迟到批次未采用")
                break
            for future in done:
                pending.pop(future)
                try:
                    result = future.result()
                    if time.monotonic() < deadline:
                        output.extend(result)
                    else:
                        errors.append("同行批次超过采集预算，结果未采用")
                except (ComparisonFetchError, requests.RequestException, ValueError, KeyError, TypeError) as exc:
                    logger.warning("同行批量取数失败：%s", exc)
                    errors.append("同行批量接口失败或分页不完整")
                job = next(iterator, None)
                if job is not None and time.monotonic() < deadline:
                    pending[executor.submit(worker, job)] = job
        return output, list(dict.fromkeys(errors))
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)


def _quote_snapshot(members: list[dict], target: str, cache: CacheManager | None,
                    refresh: bool, deadline: float) -> dict:
    # 缓存保存完整原始报价，不保存依赖某一目标的拆分；同池切换目标仍只请求一批。
    key = _key("tencent-quotes-v2", *sorted(m["symbol"] + "." + m["exchange"] for m in members))
    old, fresh = read_snapshot(cache, "peer_valuation", key, 3600)

    def derive(pool: dict, cache_status: str, errors: list[str]) -> dict:
        by_code = pool["quotes"]
        target_row = dict(by_code.get(target, {}))
        as_of = iso_date(target_row.get("as_of"))
        peers = []
        for member in members:
            if member["symbol"] == target:
                continue
            row = {**member, **by_code.get(member["symbol"], {})}
            row["reason"] = "" if as_of and iso_date(row.get("as_of")) == as_of else "估值日期不同或未取得数据"
            peers.append(row)
        return {"target": target_row, "peers": peers, "source": "腾讯批量估值", "as_of": as_of,
                "errors": errors, "cache_status": cache_status}

    if old and fresh and not refresh:
        return derive(old, "cache", [])
    jobs = [members[i:i + MAX_BATCH] for i in range(0, len(members), MAX_BATCH)]

    def fetch(batch):
        exchanges = {m["symbol"]: m["exchange"] for m in batch}
        query = ",".join(m["exchange"].lower() + m["symbol"] for m in batch)
        response = _response("https://qt.gtimg.cn/q=" + query, deadline)
        response.encoding = "gbk"
        return parse_tencent_quotes(response.text, exchanges)

    rows, errors = _parallel_batches(jobs, fetch, deadline)
    by_code = {r["symbol"]: r for r in rows}
    if len(by_code) < len(members):
        errors.append(f"估值代码覆盖 {len(by_code)}/{len(members)}，未取到的同行保留缺失")
    if errors and old:
        return derive(old, "stale", [*errors, "刷新失败，保留原日期成功估值快照"])
    pool = {"quotes": by_code}
    if not errors and by_code and time.monotonic() < deadline:
        write_snapshot(cache, "peer_valuation", key, pool, 3600)
    return derive(pool, "live", errors)


def fetch_a_industry(symbol: str, *, cache: CacheManager | None = None,
                     refresh: bool = False, comparison_cache_only: bool = False) -> list[IndustryData]:
    """精确三级同行全池，同批腾讯估值；更宽行业不混入。"""
    deadline = time.monotonic() + (0 if comparison_cache_only else COLLECTION_BUDGET)
    old, fresh = read_snapshot(cache, "peer_classification", symbol, 86400)
    notes = []
    pool = old if comparison_cache_only or (fresh and not refresh) else None
    if comparison_cache_only:
        notes.append("数据源暂时熔断，仅使用已有缓存，未发起新请求。")
        if pool is None:
            return [IndustryData(symbol=symbol, industry="未知", sector="",
                                 comparison_snapshot={"errors": ["数据源暂时熔断，缺少已验证的三级分类缓存"]})]
    if pool is None:
        try:
            profile = parse_a_profile(_response(f"https://legulegu.com/s/{symbol}", deadline).text)
            members = parse_a_members(_response("https://legulegu.com/stockdata/index-composition",
                                      deadline, params={"industryCode": profile["code"]}).text, profile["code"])
            if symbol not in {m["symbol"] for m in members}:
                raise ComparisonFetchError("目标不在已解析的三级成分池中")
            pool = {"profile": profile, "members": members,
                    "as_of": datetime.now().astimezone().isoformat()}
            if time.monotonic() >= deadline:
                raise ComparisonFetchError("三级分类超过采集预算，迟到结果未缓存")
            write_snapshot(cache, "peer_classification", symbol, pool, 86400)
        except (ComparisonFetchError, requests.RequestException, ValueError, KeyError, TypeError) as exc:
            logger.warning("精确申万三级池获取失败 %s：%s", symbol, exc)
            if not old:
                return [IndustryData(symbol=symbol, industry="未知", sector="",
                                     comparison_snapshot={"errors": ["未取得精确申万三级同行池"]})]
            pool = old
            notes.append("分类刷新失败，使用上次已验证的申万三级池；成分可能已变更")
    profile, members = pool["profile"], pool["members"]
    snapshot = _quote_snapshot(members, symbol, cache, refresh and not comparison_cache_only, deadline)
    snapshot["notes"] = notes
    snapshot["classification_as_of"] = pool["as_of"]
    peers = [m for m in members if m["symbol"] != symbol]
    by_code = {p["symbol"]: p for p in snapshot["peers"]}
    display = sorted(peers, key=lambda p: p.get("market_cap") or 0, reverse=True)[:5]
    data = IndustryData(symbol=symbol, industry=profile["level3"], sector=profile.get("level1", ""),
                        peer_scope="申万三级", peer_industry=profile["level3"],
                        resolved_sw_level1=profile.get("level1", ""), resolved_sw_level2=profile.get("level2", ""),
                        peers=[m["symbol"] for m in peers], peer_names={m["symbol"]: m["name"] for m in peers},
                        top_peers=[PeerBasicInfo(**{k: by_code.get(p["symbol"], p).get(k) for k in
                                   ("symbol", "name", "market_cap", "pe_ttm", "pb")}) for p in display],
                        comparison_ready=True, comparison_snapshot=snapshot)
    data._data_source = "乐咕申万三级+腾讯批量估值"
    data._comparison_deadline = deadline
    return [data]


def fetch_financial_rows(symbols: list[str], market: str, *, start: str,
                         deadline: float, cache: CacheManager | None = None,
                         refresh: bool = False) -> tuple[list[dict], list[str]]:
    symbols = sorted(set(symbols))
    key = _key(market, "main-indicator-v1", start, *symbols)
    old, fresh = read_snapshot(cache, "peer_financial", key, 21600)
    if old is not None and fresh and not refresh:
        return old, []
    jobs = [symbols[i:i + MAX_BATCH] for i in range(0, len(symbols), MAX_BATCH)]
    hk = market == "hk"

    def fetch(batch):
        code_key = "SECUCODE" if hk else "SECURITY_CODE"
        date_key = "STD_REPORT_DATE" if hk else "REPORT_DATE"
        codes = ",".join('"' + s + '"' for s in batch)
        params = {"reportName": "RPT_HKF10_FN_MAININDICATOR" if hk else "RPT_F10_FINANCE_MAINFINADATA",
                  "columns": "HKF10_FN_MAININDICATOR" if hk else "ALL", "quoteColumns": "",
                  "filter": f'({code_key} in ({codes}))({date_key}>=\'{start}\')',
                  "pageSize": str(PAGE_SIZE), "pageNumber": "1", "sortColumns": date_key,
                  "sortTypes": "-1", "source": "F10", "client": "PC"}
        rows, page, pages, expected = [], 1, 1, 0
        while page <= pages:
            params["pageNumber"] = str(page)
            payload = _response(HK_URL if hk else A_URL, deadline, params=params).json()
            if not isinstance(payload, dict):
                raise ComparisonFetchError("主要指标接口 JSON 不是对象")
            result = payload.get("result")
            if payload.get("success") is not True or not isinstance(result, dict):
                raise ComparisonFetchError("主要指标接口返回失败")
            new = result.get("data")
            if not isinstance(new, list):
                raise ComparisonFetchError("主要指标接口缺少数据列表")
            current_pages, count = int(result.get("pages") or 1), int(result.get("count") or 0)
            if page == 1:
                pages, expected = current_pages, count
                if pages > 30 or pages < 1 or count < 0:
                    raise ComparisonFetchError("主要指标分页超出请求范围")
            elif current_pages != pages or count != expected:
                raise ComparisonFetchError("主要指标分页统计发生变化")
            if any(not isinstance(r, dict) or str(r.get(code_key)) not in batch for r in new):
                raise ComparisonFetchError("主要指标混入未请求代码")
            rows.extend(new)
            page += 1
        if len(rows) != expected:
            raise ComparisonFetchError("主要指标分页不完整")
        return normalize_financial_rows(rows, market)

    rows, errors = _parallel_batches(jobs, fetch, deadline)
    coverage = len({r["symbol"] for r in rows})
    invalid = sum(bool(r.get("reason")) for r in rows)
    if invalid:
        errors.append(f"经营指标有 {invalid} 条期间、日期或版本未验证，不覆盖成功缓存")
    if coverage < len(symbols):
        errors.append(f"经营指标代码覆盖 {coverage}/{len(symbols)}，未取到的公司保留缺失")
    if errors and old is not None:
        return old, [*errors, "经营指标刷新失败，使用原报告期成功快照"]
    if not errors and rows and time.monotonic() < deadline:
        write_snapshot(cache, "peer_financial", key, rows, 21600)
    return rows, errors

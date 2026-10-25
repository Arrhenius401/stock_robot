"""港股通恒生二级同行池 — 东财 F10 行业估值池，过滤为港股通且已映射标的。"""
import logging
import time
from functools import partial
from typing import Any

import requests

from data.base import DataSource
from data.hk_hsics_industry import HsicsMember, load_connect_symbols, load_hsics_members
from data.peer_comparison_fetch import (
    COLLECTION_BUDGET,
    ComparisonFetchError,
    read_snapshot,
    request_with_deadline,
    write_snapshot,
)
from data.peer_comparison_rules import finite, iso_date, valuation_date
from data.schemas import IndustryData, PeerBasicInfo

logger = logging.getLogger(__name__)

POOL_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
POOL_REPORT = "RPT_PCF10_INDUSTRY_HKCVALUE"
POOL_COLUMNS = "CORRE_SECUCODE,CORRE_SECURITY_NAME,PE_TTM,PB_MQR,TYPE_NAME,TYPE_ID,REPORT_DATE"
MAX_POOL_PAGES = 20


class HsicsPeerError(RuntimeError):
    """港股同行池获取失败。"""


def build_industry_data(symbol: str, rows: list[dict[str, Any]],
                        members: dict[str, HsicsMember],
                        connect: set[str]) -> IndustryData | None:
    if symbol not in connect:
        return None
    target = members.get(symbol)
    if target is None or target.status != "mapped":
        return None

    target_rows = [
        row for row in rows
        if row.get("CORRE_SECUCODE") == symbol and row.get("TYPE_NAME") == target.sector
    ]
    target_row = max(target_rows, key=lambda r: valuation_date(r.get("REPORT_DATE")), default={})
    target_date = valuation_date(target_row.get("REPORT_DATE"))

    # 分类及港股通资格定义完整池，报价漏行不能静默改变同行分母。
    qualified = sorted(s for s, m in members.items() if s in connect and m.status == "mapped"
                       and m.sector == target.sector and s != symbol)
    peer_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        peer_symbol = str(row.get("CORRE_SECUCODE") or "")
        if peer_symbol == symbol or peer_symbol not in connect:
            continue
        peer_member = members.get(peer_symbol)
        if peer_member is None or peer_member.status != "mapped":
            continue
        if peer_member.sector != target.sector or row.get("TYPE_NAME") != target.sector:
            continue
        # 优先保留与目标同日的记录；没有同日行时保留最新原值作带日期参考。
        existing = peer_rows.get(peer_symbol)
        row_date = valuation_date(row.get("REPORT_DATE"))
        if existing is not None:
            existing_date = valuation_date(existing.get("REPORT_DATE"))
            if (bool(target_date and row_date == target_date), row_date) <= (
                    bool(target_date and existing_date == target_date), existing_date):
                continue
        peer_rows[peer_symbol] = row
    peer_valuations: dict[str, PeerBasicInfo] = {}
    for peer_symbol in qualified:
        row = peer_rows.get(peer_symbol, {})
        peer_valuations[peer_symbol] = PeerBasicInfo(
            symbol=peer_symbol, name=str(row.get("CORRE_SECURITY_NAME") or ""),
            market_cap=None,
            pe_ttm=finite(row.get("PE_TTM")),
            pb=finite(row.get("PB_MQR")),
        )

    peers = sorted(peer_valuations)
    comparison_peers = []
    for peer_symbol in peers:
        as_of = iso_date(peer_rows.get(peer_symbol, {}).get("REPORT_DATE"))
        reason = ""
        if peer_symbol not in peer_rows:
            reason = "未取得同行估值，保留资格同行及缺失状态"
        elif not target_date:
            reason = "目标估值日期无效或缺失，不参与比较"
        elif not valuation_date(as_of):
            reason = "同行估值日期无效、缺失或在未来，不参与同日统计"
        elif as_of != target_date:
            reason = "同行估值日期与目标不同，不参与同日统计"
        comparison_peers.append({**peer_valuations[peer_symbol].model_dump(), "as_of": as_of, "reason": reason})
    errors = [] if target_date else ["目标估值缺失或日期无效，未生成估值比较"]
    invalid_dates = sum(not valuation_date(r.get("REPORT_DATE")) for r in peer_rows.values())
    if invalid_dates:
        errors.append(f"港股同行估值有 {invalid_dates} 条日期无效、缺失或在未来，不覆盖成功缓存")
    quote_count = len(peer_rows) + bool(target_rows)
    if quote_count < len(qualified) + 1:
        errors.append(f"港股同行估值代码覆盖 {quote_count}/{len(qualified) + 1}，漏取公司保留缺失")
    data = IndustryData(
        symbol=symbol,
        industry=target.sector,
        sector=target.industry,
        peers=peers,
        peer_names={symbol: peer_valuations[symbol].name for symbol in peers},
        peer_scope="港股通·恒生HSICS二级",
        peer_industry=target.sector,
        resolved_sw_level1="",
        resolved_sw_level2="",
        top_peers=[],
        comparison_ready=True,
        comparison_snapshot={
            "target": {"symbol": symbol, "pe_ttm": finite(target_row.get("PE_TTM")), "pb": finite(target_row.get("PB_MQR")), "as_of": target_date},
            "peers": comparison_peers,
            "as_of": target_date,
            "source": "东财F10+恒生HSICS", "cache_status": "live",
            "notes": ["成员资格为港股通且已有恒生 HSICS 二级映射。"],
            "errors": errors,
        },
    )
    data._data_source = "东财F10+恒生HSICS"
    data._peer_valuations = peer_valuations
    data._target_pe = target_row.get("PE_TTM")
    data._target_pb = target_row.get("PB_MQR")
    data._data_date = target_date
    return data


def fetch_sector_pool(session: requests.Session,
                      symbol: str, *, deadline: float | None = None) -> tuple[list[dict[str, Any]], str]:
    deadline = deadline or time.monotonic() + COLLECTION_BUDGET
    rows: list[dict[str, Any]] = []
    page = 1
    pages = 1
    count = 0
    while page <= pages and page <= MAX_POOL_PAGES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HsicsPeerError("港股同行采集预算已用尽")
        params = {
            "reportName": POOL_REPORT,
            "columns": POOL_COLUMNS,
            "quoteColumns": "",
            "filter": f'(SECUCODE="{symbol}")',
            "pageNumber": str(page),
            "pageSize": "200",
            "sortTypes": "",
            "sortColumns": "",
            "source": "F10",
            "client": "PC",
            "v": "03445297742754925",
        }
        response = request_with_deadline(partial(session.get, POOL_URL, params=params,
                                         timeout=(min(3, remaining / 2), min(8, remaining / 2))), deadline)
        response.raise_for_status()
        if time.monotonic() >= deadline:
            raise HsicsPeerError("港股同行请求超过采集预算，迟到结果未采用")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ComparisonFetchError("东财港股行业池 JSON 不是对象")
        if not payload.get("success"):
            raise HsicsPeerError("东财港股行业池返回失败")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ComparisonFetchError("东财港股行业池 result 类型无效")
        page_rows = result.get("data")
        if not isinstance(page_rows, list) or any(not isinstance(r, dict) for r in page_rows):
            raise ComparisonFetchError("东财港股行业池 data 类型无效")
        if page == 1:
            pages = int(result.get("pages") or 1)
            count = int(result.get("count") or 0)
            if pages > MAX_POOL_PAGES or pages < 1:
                raise HsicsPeerError("港股同行池分页超出范围")
        elif int(result.get("pages") or 1) != pages or int(result.get("count") or 0) != count:
            raise HsicsPeerError("港股同行池分页统计发生变化")
        rows.extend(row for row in page_rows if isinstance(row, dict))
        page += 1
    if len(rows) != count:
        raise HsicsPeerError("东财港股行业池分页不完整")
    # 每行日期是比较证据；不得用任意同行的新日期裁掉较旧目标及其同日同行。
    target_dates = [valuation_date(row.get("REPORT_DATE")) for row in rows if row.get("CORRE_SECUCODE") == symbol]
    return rows, max(target_dates, default="")


class HKIndustryAdapter(DataSource):
    def supports(self, market: str, data_type: str) -> bool:
        return market == "hk" and data_type == "industry"

    def fetch(self, symbol: str, **_kwargs) -> list:
        cache_only = bool(_kwargs.get("comparison_cache_only", False))
        deadline = time.monotonic() + (0 if cache_only else COLLECTION_BUDGET)
        members = load_hsics_members()
        target = members.get(symbol)
        if target is None or target.status != "mapped":
            return []
        connect = {code + ".HK" for code in load_connect_symbols()}
        if symbol not in connect:
            return []
        cache = _kwargs.get("comparison_cache")
        refresh = bool(_kwargs.get("refresh_cache", False))
        # 旧快照丢失逐行日期，不能以顶层目标日期替代；新键隔离该不完整契约。
        old, fresh = read_snapshot(cache, "peer_hk_industry_v2", symbol, 3600)
        if old is not None and fresh and (not refresh or cache_only):
            data = IndustryData.model_validate(old)
            # 手动资格快照可能已更新，缓存中的成员仍须重新核对。
            qualified = sorted(s for s, m in members.items() if s in connect and s != symbol
                               and m.status == "mapped" and m.sector == target.sector)
            if symbol in connect and qualified == data.peers and data.peer_industry == target.sector:
                data.comparison_snapshot["cache_status"] = "cache"
                if cache_only:
                    data.comparison_snapshot.setdefault("notes", []).append("数据源暂时熔断，仅使用已有缓存，未发起新请求。")
                data._data_source = "东财F10+恒生HSICS"
                data._comparison_deadline = deadline
                return [data]

        def restore_cached() -> IndustryData:
            if old is None:
                restored_data = build_industry_data(symbol, [], members, connect)
            else:
                restored = IndustryData.model_validate(old)
                old_rows = [{"CORRE_SECUCODE": symbol, "TYPE_NAME": target.sector,
                             "PE_TTM": restored.comparison_snapshot.get("target", {}).get("pe_ttm"),
                             "PB_MQR": restored.comparison_snapshot.get("target", {}).get("pb"),
                             "REPORT_DATE": restored.comparison_snapshot.get("as_of", "")}]
                old_rows.extend({"CORRE_SECUCODE": p["symbol"], "CORRE_SECURITY_NAME": p.get("name", ""),
                                 "TYPE_NAME": restored.peer_industry, "PE_TTM": p.get("pe_ttm"),
                                 "PB_MQR": p.get("pb"), "REPORT_DATE": p.get("as_of", "")}
                                for p in restored.comparison_snapshot.get("peers", []))
                restored_data = build_industry_data(symbol, old_rows, members, connect)
            if restored_data is None:
                raise HsicsPeerError("港股同行资格状态变化")
            restored_data._comparison_deadline = deadline
            return restored_data

        if cache_only:
            data = restore_cached()
            data.comparison_snapshot["cache_status"] = "stale" if old else "unknown"
            data.comparison_snapshot["errors"].append("数据源暂时熔断，仅使用已有缓存，未发起新请求。")
            return [data]
        try:
            with requests.Session() as session:
                rows, _ = fetch_sector_pool(session, symbol, deadline=deadline)
            data = build_industry_data(symbol, rows, members, connect)
        except (HsicsPeerError, ComparisonFetchError, requests.RequestException, ValueError, KeyError, TypeError) as exc:
            logger.warning("港股同行估值刷新失败 %s：%s", symbol, exc)
            # 复用成功数值前再次套用当前资格和行业，避免缓存放宽成员范围。
            data = restore_cached()
            data.comparison_snapshot["cache_status"] = "stale" if old else "unknown"
            data.comparison_snapshot["errors"].append("港股同行估值刷新失败，使用原日期成功快照" if old else "港股同行估值获取失败，保留资格池用于经营比较")
        else:
            if data is not None and data.comparison_snapshot.get("errors") and old is not None:
                failed_errors = data.comparison_snapshot["errors"]
                data = restore_cached()
                data.comparison_snapshot["cache_status"] = "stale"
                data.comparison_snapshot["errors"].extend([*failed_errors, "港股同行估值刷新数据不完整，使用原日期成功快照"])
            if (data is not None and data.comparison_snapshot.get("as_of")
                    and not data.comparison_snapshot.get("errors") and time.monotonic() < deadline):
                write_snapshot(cache, "peer_hk_industry_v2", symbol, data.model_dump(mode="json"), 3600)
        if data is not None:
            data._comparison_deadline = deadline
        return [data] if data is not None else []

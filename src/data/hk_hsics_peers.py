"""港股通恒生二级同行池 — 东财 F10 行业估值池，过滤为港股通且已映射标的。"""
import logging
from typing import Any

import requests

from data.base import DataSource
from data.hk_hsics_industry import HsicsMember, load_connect_symbols, load_hsics_members
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
    if not target_rows:
        return None
    target_row = target_rows[0]

    peer_valuations: dict[str, PeerBasicInfo] = {}
    for row in rows:
        peer_symbol = str(row.get("CORRE_SECUCODE") or "")
        if peer_symbol == symbol or peer_symbol not in connect:
            continue
        peer_member = members.get(peer_symbol)
        if peer_member is None or peer_member.status != "mapped":
            continue
        if peer_member.sector != target.sector or row.get("TYPE_NAME") != target.sector:
            continue
        if peer_symbol in peer_valuations:
            continue
        peer_valuations[peer_symbol] = PeerBasicInfo(
            symbol=peer_symbol,
            name=str(row.get("CORRE_SECURITY_NAME") or ""),
            market_cap=None,
            pe_ttm=row.get("PE_TTM"),
            pb=row.get("PB_MQR"),
        )

    peers = sorted(peer_valuations)
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
    )
    data._data_source = "东财F10+恒生HSICS"
    data._peer_valuations = peer_valuations
    data._target_pe = target_row.get("PE_TTM")
    data._target_pb = target_row.get("PB_MQR")
    data._data_date = str(target_row.get("REPORT_DATE") or "").split(" ")[0]
    return data


def fetch_sector_pool(session: requests.Session,
                      symbol: str) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    page = 1
    pages = 1
    count = 0
    while page <= pages and page <= MAX_POOL_PAGES:
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
        response = session.get(POOL_URL, params=params, timeout=(8, 25))
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise HsicsPeerError("东财港股行业池返回失败")
        result = payload.get("result") or {}
        page_rows = result.get("data") or []
        if page == 1:
            pages = int(result.get("pages") or 1)
            count = int(result.get("count") or 0)
        rows.extend(row for row in page_rows if isinstance(row, dict))
        page += 1
    if not rows or len(rows) != count:
        raise HsicsPeerError("东财港股行业池分页不完整")
    dates = sorted({
        str(row.get("REPORT_DATE") or "").split(" ")[0]
        for row in rows if row.get("REPORT_DATE")
    })
    if not dates:
        raise HsicsPeerError("东财港股行业池缺少报告日期")
    latest = dates[-1]
    rows = [
        row for row in rows
        if str(row.get("REPORT_DATE") or "").split(" ")[0] == latest
    ]
    return rows, latest


class HKIndustryAdapter(DataSource):
    def supports(self, market: str, data_type: str) -> bool:
        return market == "hk" and data_type == "industry"

    def fetch(self, symbol: str, **_kwargs) -> list:
        members = load_hsics_members()
        target = members.get(symbol)
        if target is None or target.status != "mapped":
            return []
        connect = {code + ".HK" for code in load_connect_symbols()}
        with requests.Session() as session:
            rows, _ = fetch_sector_pool(session, symbol)
        data = build_industry_data(symbol, rows, members, connect)
        return [data] if data is not None else []

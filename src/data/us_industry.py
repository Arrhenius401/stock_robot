"""东财美股 F10 行业采集；未核实的行情字段不参与经营指标派生。"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import requests

from data.collection_state import ValueResult, make_record
from data.schemas import IndustryData

logger = logging.getLogger(__name__)
PROFILE_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
SOURCE = "东财美股 F10 行业（BELONG_INDUSTRY）"


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


def us_industry_result(symbol: str) -> ValueResult[IndustryData]:
    """行业身份可展示；行情口径未核实期间比较始终关闭。"""
    stamp = datetime.now().astimezone()
    try:
        if re.fullmatch(r"[A-Z]+(?:\.[A-Z])?", symbol) is None:
            raise ValueError("美股代码格式无效")
        ticker = _code(symbol)
        profiles = _profiles(f'(SECURITY_CODE="{ticker}")')
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
        members = filter_members(_profiles(f'(BELONG_INDUSTRY="{industry}")'), industry, symbol)
        reason = "同行行情字段及 TTM 营收口径尚未核实，未进行经营指标派生"
        value = IndustryData(
            symbol=symbol, industry=industry, sector="", peers=list(members),
            peer_names=members, peer_scope=SOURCE, peer_industry=industry,
            comparison_ready=False,
            comparison_snapshot={
                "source": SOURCE, "as_of": stamp.isoformat(), "basis": "TTM",
                "candidate_count": len(members), "revenue": None,
                "errors": [reason], "attempts": [{"source": SOURCE, "status": "ok"},
                    {"source": "东财美股同行行情", "status": "unverified", "reason": reason}],
            },
        )
        value._data_source = SOURCE
        return ValueResult(value, make_record(
            SOURCE, "partial", collected_at=stamp, error_kind="unverified_basis", error_message=reason,
        ), [reason])
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        logger.warning("美股行业采集失败 %s: %s", symbol, exc)
        return ValueResult(None, make_record(
            SOURCE, "failed", collected_at=stamp, error_kind=type(exc).__name__, error_message=str(exc),
        ), [str(exc)])

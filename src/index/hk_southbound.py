"""港股通成交净买额采集，保持沪深渠道与指数身份分离。"""
import logging
from datetime import date
from typing import Any

import akshare as ak

from data.collection_state import ValueResult, make_record
from index.strategy_data import finite_number

logger = logging.getLogger(__name__)


def parse_southbound(rows: list[dict[str, Any]], as_of: date, *, max_age_days: int) -> dict[str, Any]:
    """仅接受相同日期、不同渠道的南向真实数值。"""
    by_day: dict[date, dict[str, float]] = {}
    for row in rows:
        if row.get("资金方向") != "南向" or str(row.get("交易状态")) == "4":
            continue
        board = str(row.get("板块", "")).replace("（", "(").replace("）", ")")
        channel = {"港股通(沪)": "sh", "港股通(深)": "sz"}.get(board)
        if channel is None:
            continue
        try:
            day = date.fromisoformat(str(row["交易日"])[:10])
        except (TypeError, ValueError, KeyError):
            continue
        value = finite_number(row.get("成交净买额"))
        if value is None or day > as_of:
            continue
        values = by_day.setdefault(day, {})
        if channel in values and values[channel] != value:
            raise ValueError("同日港股通渠道数据冲突")
        values[channel] = value
    complete = [day for day, values in by_day.items() if set(values) == {"sh", "sz"}]
    if not complete:
        raise ValueError("港股通沪深渠道数据不完整")
    day = max(complete)
    if (as_of - day).days > max_age_days:
        raise ValueError("港股通数据已过期")
    return {"date": day, **by_day[day]}


def fetch_southbound(as_of: date, *, max_age_days: int) -> ValueResult[dict[str, Any]]:
    """通过现有 AkShare 采集；AkShare 返回值为亿元：东财网页将原始值乘以一万元展示。"""
    try:
        frame: Any = ak.stock_hsgt_fund_flow_summary_em()
        value = parse_southbound(frame.to_dict("records"), as_of, max_age_days=max_age_days)
        return ValueResult(value, make_record("东方财富港股通", "complete"))
    except Exception as exc:  # noqa: BLE001 — 第三方接口隔离，失败明确记录
        logger.warning("港股通成交净买额采集失败：%s", exc)
        return ValueResult(None, make_record("东方财富港股通", "failed", error_kind="fetch_failed",
                           error_message="港股通数据不可用或已过期"), [str(exc)])

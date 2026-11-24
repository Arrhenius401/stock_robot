"""A 股东财日频估值历史；只返回可核实字段，不做跨源拼接。"""
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import akshare as ak

from data.collection_state import ValueResult, make_record
from utils.numbers import is_finite_number

logger = logging.getLogger(__name__)
SOURCE = "东方财富日频估值历史"
_REQUIRED = {"数据日期", "PE(TTM)", "市净率", "市销率", "总市值", "总股本"}


@dataclass(frozen=True)
class ValuationHistoryRow:
    trade_date: date
    close: float | None
    pe_ttm: float | None
    pb: float | None
    ps_ttm: float | None
    market_cap: float | None
    total_shares: float | None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if is_finite_number(number) else None


def parse_history(frame: Any, today: date | None = None) -> list[ValuationHistoryRow]:
    if frame is None or getattr(frame, "empty", True):
        raise ValueError("东财估值历史为空")
    missing = _REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"东财估值历史缺少列：{', '.join(sorted(missing))}")
    cutoff = today or datetime.now().astimezone().date()
    rows: dict[date, ValuationHistoryRow] = {}
    conflicts: set[date] = set()
    for record in frame.to_dict("records"):
        day = _date(record.get("数据日期"))
        if day is None or day > cutoff:
            continue
        row = ValuationHistoryRow(
            trade_date=day,
            close=_number(record.get("当日收盘价")),
            pe_ttm=_number(record.get("PE(TTM)")),
            pb=_number(record.get("市净率")),
            ps_ttm=_number(record.get("市销率")),
            market_cap=_number(record.get("总市值")),
            total_shares=_number(record.get("总股本")),
        )
        previous = rows.get(day)
        if previous is not None and previous != row:
            conflicts.add(day)
            continue
        rows[day] = row
    for day in conflicts:
        rows.pop(day, None)
    if not rows:
        raise ValueError("东财估值历史没有有效日期")
    return [rows[key] for key in sorted(rows)]


def load_history(symbol: str) -> ValueResult[list[ValuationHistoryRow]]:
    if len(symbol) != 6 or not symbol.isdigit():
        return ValueResult(None, make_record(
            SOURCE, "failed", error_kind="invalid_symbol",
            error_message="A 股代码必须为六位数字"))
    try:
        rows = parse_history(ak.stock_value_em(symbol=symbol))
        return ValueResult(rows, make_record(SOURCE, "complete",
                                             collected_at=datetime.now().astimezone()))
    except Exception as error:  # noqa: BLE001 — 第三方估值历史边界
        logger.warning("东财估值历史失败 %s: %s", symbol, type(error).__name__)
        return ValueResult(None, make_record(
            SOURCE, "failed", error_kind=type(error).__name__,
            error_message="东财估值历史暂不可用"), [str(error)])

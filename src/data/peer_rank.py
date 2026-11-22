"""完整同日同行池的总市值排名，不复用头部或分层样本。"""
from typing import Any

from data.peer_comparison_rules import valuation_date
from utils.numbers import is_positive_finite


def market_cap_rank(snapshot: dict[str, Any], symbols: list[str], target: str) -> dict[str, Any]:
    expected = set(symbols) | {target}
    result = {"rank": None, "total": len(expected), "as_of": "", "reason": "完整同日市值覆盖不足"}
    rows = [snapshot.get("target", {}), *snapshot.get("peers", [])]
    by_code = {}
    for row in rows:
        symbol = row.get("symbol")
        if symbol in expected:
            if symbol in by_code:
                return result
            by_code[symbol] = row
    if set(by_code) != expected:
        return result
    dates = {valuation_date(row.get("as_of")) for row in by_code.values()}
    if len(dates) != 1 or not next(iter(dates)) or not all(
            is_positive_finite(row.get("market_cap")) for row in by_code.values()):
        return result
    if snapshot.get("as_of") and valuation_date(snapshot["as_of"]) != next(iter(dates)):
        return result
    cap = by_code[target]["market_cap"]
    return {"rank": 1 + sum(row["market_cap"] > cap for row in by_code.values()),
            "total": len(expected), "as_of": next(iter(dates)), "reason": ""}

"""同日有效同行市值的部分覆盖排名；缺失同行计入覆盖缺口。"""
from typing import Any

from data.peer_comparison_rules import valuation_date
from utils.numbers import is_positive_finite


def market_cap_rank(snapshot: dict[str, Any], symbols: list[str], target: str) -> dict[str, Any]:
    expected = set(symbols) | {target}
    base = {"rank": None, "total": 0, "pool_total": len(expected),
            "coverage": f"0/{len(expected)}", "as_of": "", "reason": "完整同日市值覆盖不足"}
    target_row = snapshot.get("target") or {}
    if target_row.get("symbol") != target:
        return {**base, "reason": "目标记录缺失或身份不匹配"}
    by_code: dict[str, dict[str, Any]] = {target: target_row}
    for row in snapshot.get("peers", []):
        symbol = row.get("symbol")
        if symbol is None or symbol not in expected or symbol == target:
            return {**base, "reason": "同行数据含未知或重复代码"}
        if symbol in by_code:
            return {**base, "reason": "同行池存在重复代码"}
        by_code[symbol] = row
    target_date = valuation_date(target_row.get("as_of"))
    if not target_date or not is_positive_finite(target_row.get("market_cap")):
        return {**base, "reason": "目标市值缺失或日期无效"}
    if snapshot.get("as_of") and valuation_date(snapshot["as_of"]) != target_date:
        return {**base, "reason": "快照日期与目标日期不一致"}
    valid = [row for code, row in by_code.items()
             if code != target and valuation_date(row.get("as_of")) == target_date
             and is_positive_finite(row.get("market_cap"))]
    total = 1 + len(valid)
    if total == 1:
        return {**base, "reason": "没有同日有效同行市值"}
    rank = 1 + sum(row["market_cap"] > target_row["market_cap"] for row in valid)
    return {"rank": rank, "total": total, "pool_total": len(expected),
            "coverage": f"{total}/{len(expected)}", "as_of": target_date,
            "reason": "" if total == len(expected) else "部分同行缺少同日有效市值"}

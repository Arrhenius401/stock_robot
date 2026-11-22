"""市值排名必须覆盖完整同行池、同日、包含目标，并保留并列。"""
from copy import deepcopy

import pytest

from data.peer_rank import market_cap_rank


def snapshot():
    return {"target": {"symbol": "A", "market_cap": 20, "as_of": "2026-11-20"},
            "peers": [{"symbol": "B", "market_cap": 30, "as_of": "2026-11-20"},
                      {"symbol": "C", "market_cap": 20, "as_of": "2026-11-20"}]}


def test_full_pool_and_tied_rank():
    actual = market_cap_rank(snapshot(), ["B", "C"], "A")
    assert actual == {"rank": 2, "total": 3, "as_of": "2026-11-20", "reason": ""}


@pytest.mark.parametrize("failure", ["missing", "date", "negative", "duplicate", "unknown_member",
                                   "future", "snapshot_date"])
def test_partial_or_invalid_pool_has_no_rank(failure):
    data = deepcopy(snapshot())
    symbols = ["B", "C"]
    if failure == "missing":
        data["peers"].pop()
    elif failure == "date":
        data["peers"][0]["as_of"] = "2026-11-19"
    elif failure == "negative":
        data["peers"][0]["market_cap"] = -1
    elif failure == "duplicate":
        data["peers"].append(data["peers"][0])
    elif failure == "unknown_member":
        symbols.append("D")
    elif failure == "future":
        data["target"]["as_of"] = "2099-01-01"
        for row in data["peers"]:
            row["as_of"] = "2099-01-01"
    else:
        data["as_of"] = "2026-11-19"
    assert market_cap_rank(data, symbols, "A")["rank"] is None

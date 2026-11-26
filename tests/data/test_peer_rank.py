"""同日有效市值同行的部分覆盖排名。"""
import pytest

from data.peer_rank import market_cap_rank


def snapshot():
    return {"target": {"symbol": "A", "market_cap": 20, "as_of": "2026-11-20"},
            "peers": [{"symbol": "B", "market_cap": 30, "as_of": "2026-11-20"},
                      {"symbol": "C", "market_cap": 20, "as_of": "2026-11-20"}]}


def test_full_pool_and_tied_rank():
    assert market_cap_rank(snapshot(), ["B", "C"], "A") == {
        "rank": 2, "total": 3, "pool_total": 3, "coverage": "3/3",
        "as_of": "2026-11-20", "reason": ""}


def test_missing_peer_is_excluded_and_counted_in_coverage():
    data = snapshot()
    data["peers"].pop()  # 移除 C(20)，保留 B(30)
    result = market_cap_rank(data, ["B", "C"], "A")
    assert result["rank"] == 2 and result["total"] == 2
    assert result["pool_total"] == 3 and result["coverage"] == "2/3"
    assert "部分同行" in result["reason"]


def test_tie_after_removing_higher_peer_keeps_first_rank():
    data = snapshot()
    data["peers"].pop(0)  # 移除 B(30)，保留 C(20)
    result = market_cap_rank(data, ["B", "C"], "A")
    assert result["rank"] == 1 and result["total"] == 2
    assert result["coverage"] == "2/3"


@pytest.mark.parametrize("mutation", ["duplicate", "unknown_row", "target_missing", "no_peer"])
def test_integrity_failures_keep_no_rank(mutation):
    data = snapshot()
    if mutation == "duplicate":
        data["peers"].append(dict(data["peers"][0]))
    elif mutation == "unknown_row":
        data["peers"].append({"symbol": "X", "market_cap": 40, "as_of": "2026-11-20"})
    elif mutation == "target_missing":
        data["target"] = {}
    else:
        data["peers"] = [{"symbol": "B", "market_cap": None, "as_of": "2026-11-20"},
                         {"symbol": "C", "market_cap": -1, "as_of": "2026-11-20"}]
    assert market_cap_rank(data, ["B", "C"], "A")["rank"] is None


@pytest.mark.parametrize("mutation", ["date", "negative"])
def test_invalid_peer_is_excluded_from_rank(mutation):
    data = snapshot()
    data["peers"][0]["as_of" if mutation == "date" else "market_cap"] = "2026-11-19" if mutation == "date" else -1
    result = market_cap_rank(data, ["B", "C"], "A")
    assert result["rank"] == 1
    assert result["coverage"] == "2/3"


def test_snapshot_date_mismatch_has_no_rank():
    data = snapshot()
    data["as_of"] = "2026-11-19"
    assert market_cap_rank(data, ["B", "C"], "A")["rank"] is None


def test_rank_uses_independent_market_cap_snapshot():
    caps = snapshot()
    data = {'as_of': '2026-10-23', 'market_cap_snapshot': caps}
    rank = market_cap_rank(data, ['B', 'C'], 'A')
    assert rank['as_of'] == '2026-11-20'
    assert rank['rank'] == 2

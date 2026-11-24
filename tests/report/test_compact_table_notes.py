"""表注保留必要的财务口径，避免推断缺失数值。"""

from report.table_notes import prepare_table


def test_missing_margin_is_not_derived_from_parent_profit():
    view = prepare_table("financial", {"net_profit": 10, "revenue": 100, "gross_margin": .5,
                                       "net_margin": None})
    assert view["metrics"]["净利率²"] == "暂无数据"


def test_unknown_amount_currency_cannot_be_replaced_by_directory_currency():
    view = prepare_table("financial", {"amount_basis":
        "金额币种未核实；目录列报币种：HKD（不等同于金额币种）"})
    assert "金额币种：HKD" not in str(view)
    assert "金额币种未核实" in str(view)


def test_profit_statement_scope_and_derived_margin_are_not_hidden():
    view = prepare_table("financial", {"gross_margin": .5,
        "amount_basis": "金额币种：USD（仅代表已核实的利润表金额）",
        "gross_margin_basis": "计算值：毛利÷营运收入；与营业额口径不同",
        "cash_flow_source": "期间或币种证据不足", "operating_cash_flow": None})
    text = str(view["notes"])
    assert "利润表币种" in text and "其他报表" in text
    assert "营运收入" in text and "派生值" in text
    assert "现金流期间或币种证据不足" in text


def test_valuation_window_and_derived_restriction_remain_in_notes():
    from report.table_notes import valuation_window_notes

    notes = valuation_window_notes("估值数据截至 2026-11-20；日期不同另注。 PE、PB分位区间：2025-01-01 至 2026-11-20，420个有效样本；以本次财务分母派生估值，仅参考，不保证覆盖上市以来完整历史。")
    text = str(notes)
    assert "2025-01-01" in text and "420" in text
    assert "派生" in text and "仅参考" in text and "不代表上市以来" in text


def test_compact_peer_table_does_not_invent_rank():
    from report.peer_comparison import format_peer_comparison_markdown

    comparison = {"scope": "申万三级·机器人", "peer_count": 17, "valuation": {},
                  "periods": {}, "market_cap_rank": {"rank": 1, "total": 5, "as_of": "2026-11-20"}}
    md = format_peer_comparison_markdown(comparison, compact=True)
    assert "| 同行市值排名⁹ | 暂缺 |" in md
    assert "指标口径与统计方法" not in md and "#### 数据来源与统计口径" not in md


def test_margin_cache_refreshes_once_without_promoting_missing_value():
    import time
    from datetime import date

    from core.resilient_collection import ResilientCollector, Snapshot
    from data.cache import CacheEntry
    from data.collection_state import make_record
    from data.schemas import FinancialData

    row = FinancialData(symbol="688836", fiscal_quarter=date(2026, 9, 30), precision_version=1,
                        financial_source="同花顺财报", revenue=100)
    snap = Snapshot([row], make_record("同花顺财报", "complete"), CacheEntry("[]", time.time(), 3600, None))
    assert not ResilientCollector._fresh(snap)
    row.net_margin_basis = "未取得源披露净利率"
    assert ResilientCollector._fresh(snap)
    assert row.net_margin is None

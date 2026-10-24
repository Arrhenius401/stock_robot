"""同行比较的网页数据与 Markdown 必须共享完整证据。"""
from copy import deepcopy

import pytest

from data.peer_comparison_rules import normalize_financial_rows, period_comparison
from data.schemas import AnalysisContext, AnalysisResult
from report.builder import ReportBuilder
from report.payload import build_stock_payload


def comparison_sample():
    """百分数按 12.5 = 12.5% 存储，不沿用财务维度的小数口径。"""
    def metric(key, label, target, unit="percent", count=5):
        return {"key": key, "label": label, "unit": unit, "target": target,
                "median": 10.5 if count >= 5 else None,
                "percentile": 60 if count >= 5 else None, "sample_count": count,
                "status": "ok" if count >= 5 else "partial",
                "reason": "" if count >= 5 else "样本不足：至少需要 5 家有效同行"}

    peers = [{"symbol": f"00000{i}", "name": f"同行{i}", "pe_ttm": -8 if i == 1 else 20,
              "pb": None if i == 2 else 2.5, "revenue_growth_yoy": -5.5,
              "net_margin": 10.2, "roe": 8.5, "start": "2025-01-01", "end": "2025-12-31",
              "reason": "非正 PE 不参与统计" if i == 1 else ""} for i in range(1, 7)]
    annual = {"label": "2025 年报", "start": "2025-01-01", "end": "2025-12-31",
              "basis": "年度累计；加权 ROE", "source": "经营来源",
              "metrics": [metric("revenue_growth_yoy", "营收同比", 12.5),
                          metric("net_margin", "净利率", -2.5), metric("roe", "ROE", 15.5, count=3)],
              "peers": peers}
    latest = deepcopy(annual)
    latest.update(label="2026 中报", start="2026-01-01", end="2026-06-30", basis="半年度累计；加权 ROE")
    latest["metrics"][0]["target"] = 6.25
    latest["peers"] = [{**p, "start": "2026-01-01", "end": "2026-06-30"} for p in peers]
    return {"version": 1, "scope": "申万三级：示例行业", "peer_count": 6,
            "valuation": {"as_of": "2026-10-23", "source": "估值来源",
                          "metrics": [metric("pe_ttm", "PE TTM", 22.2, "multiple"),
                                      metric("pb", "PB", 3.1, "multiple")], "peers": peers},
            "periods": {"annual": annual, "latest": latest}, "notes": ["统计已排除目标公司"], "errors": []}


@pytest.mark.parametrize("market", ["a-shares", "hk"])
def test_peer_comparison_markdown_and_payload_preserve_both_periods_and_all_peers(market):
    comparison = comparison_sample()
    original = deepcopy(comparison)
    result = AnalysisResult(dimension="industry", status="partial", summary="同行比较",
                            metrics={"comparison": comparison, "industry_median_pe": 9999,
                                     "top_peers": [{"symbol": "旧同行"}]})
    markdown = ReportBuilder().build("000001", "目标", [result], {}, market="港股" if market == "hk" else "A 股")
    for expected in ["估值对比", "完整年度", "最新一期", "2025 年报", "2026 中报", "12.50%", "6.25%",
                     "-2.50%", "样本不足", "不计分", "估值来源", "经营来源", "2026-10-23",
                     "半年度累计", "加权 ROE", "同行6", "非正 PE 不参与统计", "暂无数据"]:
        assert expected in markdown
    assert "9999" not in markdown and "旧同行" not in markdown
    assert "1250.00%" not in markdown
    payload = build_stock_payload("000001", "目标", [result], {}, AnalysisContext(symbol="000001", name="目标", market=market))
    assert payload["dimensions"]["industry"]["metrics"]["comparison"] == original
    assert comparison == original


def test_peer_comparison_markdown_failure_keeps_valuation_and_does_not_hide_reason():
    comparison = comparison_sample()
    comparison["periods"]["latest"] = {"label": "最新一期", "metrics": [], "peers": [], "reason": "经营接口超时"}
    comparison["errors"] = ["经营接口超时"]
    result = AnalysisResult(dimension="industry", status="partial", summary="", metrics={"comparison": comparison})
    markdown = ReportBuilder().build("000001", "目标", [result], {})
    assert "经营接口超时" in markdown
    assert "22.20" in markdown


def test_legacy_industry_report_still_shows_original_metrics():
    result = AnalysisResult(dimension="industry", status="ok", summary="", metrics={"industry": "银行", "peer_count": 8})
    markdown = ReportBuilder().build("000001", "目标", [result], {})
    assert "银行" in markdown and "8" in markdown


def missing_latest_comparison():
    """复现源只返回旧年报，但目标已披露更新期间的真实规则输出。"""
    rows = normalize_financial_rows([{
        "SECURITY_CODE": "600519", "SECURITY_NAME_ABBR": "目标公司",
        "REPORT_DATE": "2025-12-31", "REPORT_TYPE": "年报",
        "TOTALOPERATEREVETZ": 12.5, "XSJLL": 15.2, "ROEJQ": 20.3,
    }], "a-shares")
    comparison = comparison_sample()
    comparison["periods"]["latest"] = period_comparison(
        "latest", rows, "600519", [], expected_latest="2026-06-30")
    comparison["notes"] = []
    comparison["errors"] = []
    return comparison


def test_latest_period_reason_is_visible_above_empty_metric_summary():
    comparison = missing_latest_comparison()
    block = comparison["periods"]["latest"]
    assert len(block["metrics"]) == 3
    assert all(metric["target"] is None for metric in block["metrics"])
    result = AnalysisResult(dimension="industry", status="partial", summary="", metrics={"comparison": comparison})
    markdown = ReportBuilder().build("600519", "目标公司", [result], {})
    latest_section = markdown.split("#### 经营表现 · 最新一期", 1)[1].split("#### 数据来源", 1)[0]
    assert block["reason"] in latest_section.split("| 指标", 1)[0]
    assert "2026-06-30" in latest_section
    assert "旧期参考·年报" in latest_section
    assert "最新一期·年报" not in latest_section

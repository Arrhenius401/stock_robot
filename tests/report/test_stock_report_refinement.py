"""个股报告精简展示的确定性契约。"""
from copy import deepcopy
from typing import Literal

import pytest

from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    DimensionSufficiency,
    SufficiencyLevel,
)
from report.builder import ReportBuilder
from report.payload import build_stock_payload
from report.scoring import build_report
from report.signal import SignalConfig


def scored_context():
    ctx = AnalysisContext(symbol="600519", name="样本")
    for key in ("financial", "price", "valuation"):
        setattr(ctx.sufficiency, key, DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT))
    dims: tuple[Literal["financial", "technical", "valuation"], ...] = ("financial", "technical", "valuation")
    results = [AnalysisResult(dimension=dim, status="ok", summary="已有事实", score=8) for dim in dims]
    return ctx, results


def test_six_chapters_and_no_roe_history_or_action():
    ctx, results = scored_context()
    results[0].metrics = {"roe": .16, "roe_trend": [{"quarter": "2026-03-31", "roe": .1}],
                          "operating_trends": ["同报告期毛利率连续下降"], "trend_note": ""}
    text = build_report(ctx.symbol, ctx.name, results, {"bulk": "## 1.核心判断\n已有事实"}, ctx,
                        signal_cfg=SignalConfig(thresholds={"attack": 8.5, "watch": 5}))
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == ["## 一、标的基础概况", "## 二、五大维度量化数据", "## 三、综合评分",
                        "## 四、综合研判", "## 五、数据覆盖与时效", "## 六、免责声明"]
    assert "2026-03-31" not in text and "同报告期毛利率连续下降" in text
    assert "模型信号：观望" in text and "8.5" in text
    assert text.index("模型信号：") > text.index("## 四、综合研判")
    assert "建议仓位" not in text and "可考虑建仓" not in text
    assert "趋势分析限制" not in text


@pytest.mark.parametrize("state", ["disabled", "config", "failed"])
def test_payload_keeps_honest_quantitative_fallback(state):
    ctx, results = scored_context()
    payload = build_stock_payload(ctx.symbol, ctx.name, results, {}, ctx,
        no_llm=state == "disabled", llm_available=state != "config", signal_cfg=SignalConfig())
    assert payload["commentary_status"] == state
    assert "量化摘要" in payload["commentary"] and "8.0/10" in payload["commentary"]
    assert set(payload["signal"]) == {"level", "label", "basis"}
    assert isinstance(payload["signal"]["basis"], str)
    assert "summary_rows" in payload["data_quality"] and "limitations" in payload["data_quality"]


def test_compact_quality_preserves_failures_stale_and_reference_without_mutation():
    from report.presentation import compact_quality
    quality = {"dimensions": [{"key": "financial", "title": "财务", "status": "ok", "fields": [
        {"label": "样本范围", "value": "12期财报"}, {"label": "数据来源", "value": "备用源"},
        {"label": "数据截止", "value": "2026-06-30"},
        {"label": "缓存状态", "value": "过期缓存 · 原采集时间 2026-10-20"},
        {"label": "采集限制", "value": "主源请求超时，使用备用源"},
        {"label": "最新报告字段来源", "value": "主源：ROE；备用源：净利润"},
        {"label": "历史参考（不计分）", "value": "旧数据不参与本次评分", "reference_only": True},
        {"label": "参考报告期", "value": "2025-12-31", "reference_only": True},
    ]}]}
    original = deepcopy(quality)
    compact = compact_quality(quality)
    assert quality == original
    assert set(compact["summary_rows"][0]) == {"key", "title", "status", "sample", "cutoff", "source"}
    assert compact["summary_rows"][0]["source"] == "备用源"
    assert compact["summary_rows"][0]["cutoff"] == "2026-06-30"
    notes = " ".join(item["value"] for item in compact["limitations"])
    assert "主源请求超时" in notes and "过期缓存" in notes and "不参与本次评分" in notes
    assert "2025-12-31" in notes and "原采集时间 2026-10-20" in notes
    assert "主源：ROE；备用源：净利润" in notes
    assert compact_quality(compact) == compact


def test_quality_range_avoids_repeated_cutoff_and_entity_codes():
    quality = {"dimensions": [{"key": "technical", "title": "技术", "status": "ok", "fields": [
        {"label": "样本范围", "value": "166个交易日（2026-02-24 至 2026-10-28）"},
        {"label": "数据截止", "value": "2026-10-28"},
    ]}]}
    text = ReportBuilder().build("600519", "样本", [], {}, data_quality=quality)
    assert "166个交易日（2026-02-24 至 2026-10-28）；截至 2026-10-28" not in text
    assert "2026&#45;10&#45;28" not in text and "2026-10-28" in text


def test_quality_limitations_deduplicate_repeated_clauses_per_dimension():
    from report.presentation import compact_quality
    quality = {"dimensions": [{"key": "sentiment", "title": "舆情", "status": "unavailable", "fields": [
        {"label": "样本范围", "value": "10条新闻与公告"},
        {"label": "评估说明", "value": "未配置舆情标注模型，暂不计分"},
        {"label": "缺失原因", "value": (
            "未配置舆情标注模型，暂不计分；有限样本，不保证覆盖窗口内全部新闻公告；"
            "近30天有效评估 0 条；未配置舆情标注模型，暂不计分")},
    ]}]}
    notes = [item["value"] for item in compact_quality(quality)["limitations"]]
    joined = "；".join(notes)
    assert joined.count("未配置舆情标注模型，暂不计分") == 1
    assert "有限样本，不保证覆盖窗口内全部新闻公告" in joined
    assert "近30天有效评估 0 条" in joined


def test_financial_notes_outside_table_and_no_duplicate_missing_ps():
    results = [AnalysisResult(dimension="financial", status="ok", summary="", metrics={
        "roe": .16, "amount_basis": "CNY口径", "gross_margin_basis": "摘要披露", "trend_note": ""}),
        AnalysisResult(dimension="valuation", status="partial", summary="", metrics={
            "ps_ttm": None, "valuation_notes": ["PS(TTM)：暂无数据"]})]
    text = ReportBuilder().build("600519", "样本", results, {})
    assert not any(line.startswith("|") and ("CNY口径" in line or "摘要披露" in line) for line in text.splitlines())
    assert "财务金额口径：CNY口径" in text and "毛利率计算口径：摘要披露" in text
    assert "- PS(TTM)：暂无数据" not in text


def test_score_detail_copy_preserves_values_without_duplicate_normalization():
    from report.presentation import compact_score_detail
    original = "PE 分位 70.30%，得 1/4 分；有效子项 5.00/10.00 分，按原相对权重折算为 5.0/10"
    shown = compact_score_detail(original)
    assert shown == "PE 分位 70.30%，得 1/4 分"
    assert "有效子项" in original


def test_peer_empty_notes_and_repeated_period_are_removed_without_losing_warning():
    from report.peer_comparison import format_peer_comparison_markdown
    block = {"label": "完整年度（2025-01-01 至 2025-12-31）", "start": "2025-01-01",
             "end": "2025-12-31", "metrics": [{"label": "ROE", "target": 20, "sample_count": 8}]}
    original = deepcopy(block)
    comparison = {"periods": {"annual": block}, "errors": ["存在部分同行接口失败"]}
    text = format_peer_comparison_markdown(comparison)
    assert "| 说明 |" not in text
    assert text.count("2025-01-01 至 2025-12-31") == 1
    assert "存在部分同行接口失败" in text and block == original
    block["metrics"][0]["reason"] = "同口径样本有限"
    text = format_peer_comparison_markdown(comparison)
    assert "| 说明 |" in text and "同口径样本有限" in text


def test_static_pe_display_name_does_not_invent_values():
    text = ReportBuilder().build("600519", "样本", [AnalysisResult(
        dimension="valuation", status="ok", summary="", metrics={"pe_static": 20.2})], {})
    assert "PE（静）" in text and "20.20" in text
    assert "扣非PE" not in text


def test_signal_basis_retains_exact_threshold_at_rounding_boundary():
    from report.presentation import signal_presentation
    config = SignalConfig(thresholds={"attack": 7.0000001, "watch": 4.0000001})
    signal = signal_presentation(7.0, config)
    assert signal["label"] == "观望"
    assert "≥7.0000001分为进攻" in signal["basis"]
    assert "≥4.0000001且<7.0000001分为观望" in signal["basis"]
    assert "≥7分为进攻" not in signal["basis"]


def test_dimension_risks_localize_and_deduplicate_including_sentiment():
    results = [AnalysisResult(dimension="financial", status="ok", summary="财务事实", metrics={"roe": .1},
                              risk_flags=["high_debt", "high_debt", "资产负债率偏高"]),
               AnalysisResult(dimension="sentiment", status="partial", summary="新闻事实", metrics={
                   "assessment_reason": "已评估有限样本"},
                   risk_flags=["major_negative_news", "major_negative_news", "存在重大负面舆情"])]
    original = deepcopy(results)
    text = ReportBuilder().build("600519", "样本", results, {"bulk": "核心判断"},
                                 scoring_method="five-dimension-v1")
    financial = text.split("### 1. 财务量化数据", 1)[1].split("### 2. 技术面量化数据", 1)[0]
    sentiment = text.split("### 5. 舆情量化数据", 1)[1].split("## 三、综合评分", 1)[0]
    commentary = text.split("## 四、综合研判", 1)[1].split("## 五、数据覆盖与时效", 1)[0]
    assert financial.count("资产负债率偏高") == 1
    assert sentiment.count("存在重大负面舆情") == 1
    assert "high_debt" not in text and "major_negative_news" not in text
    assert "风险标签清单" not in commentary and "重大负面舆情" not in commentary
    assert results == original


def test_short_history_does_not_assert_listing_coverage_without_listing_date():
    from datetime import date

    from data.schemas import DailyValuationPoint, EnrichedValuation
    from report.valuation_dates import stock_date_presentation

    ctx = AnalysisContext(symbol="600519", name="样本", enriched_valuation=EnrichedValuation(
        daily_points=[DailyValuationPoint(trade_date=date(2026, 10, 27), close=10, pe=20, pb=2)]))
    text = stock_date_presentation(ctx, {"pe_percentile": 0, "pb_percentile": 0})["cutoff"]
    assert "不保证覆盖上市以来完整历史" in text
    assert "不代表上市以来完整历史" not in text


def test_unavailable_quality_dependency_is_not_an_error_reason():
    from report.presentation import compact_quality

    result = compact_quality({"dimensions": [{"title": "估值", "status": "unavailable", "fields": [
        {"label": "依赖说明", "value": "由行情和财务派生"},
    ]}]})
    assert {"dimension": "估值", "label": "原因", "value": "未记录"} in result["limitations"]
    assert any(item["value"] == "由行情和财务派生" for item in result["limitations"])

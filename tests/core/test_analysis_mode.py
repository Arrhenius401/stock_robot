"""额外分析模式贯穿计算、报告与指数输出，不以隐藏页面替代执行门控。"""
from typing import Any, Literal

import pytest

from analysis.base import AnalysisModule
from core.pipeline import Pipeline
from core.registry import Registry
from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    AnalysisTarget,
    DimensionSufficiency,
    IndexAnalysisContext,
    SufficiencyLevel,
)
from index.build_single import render_index_report_markdown
from index.pipeline import IndexPipeline
from report.payload import build_stock_payload
from report.quality import build_data_quality
from report.scoring import build_report
from report.signal import SignalConfig
from utils.config import Config


class RecordingAnalyzer(AnalysisModule):
    received: Any = "未执行"

    @property
    def dimension(self) -> Literal["technical"]:
        return "technical"

    def analyze(self, context, config=None):
        self.received = config
        return AnalysisResult(dimension="technical", status="ok", score=8,
                              summary="偏多", metrics={"latest_close": 12, "ma_20": 11},
                              risk_flags=["bearish_ma"])


def stock_context(enabled: bool, market: str = "a-shares") -> AnalysisContext:
    ctx = AnalysisContext(symbol="00700.HK" if market == "hk" else "600519", name="样例",
                          market=market, extra_analysis_enabled=enabled)
    for key in ("financial", "price", "valuation"):
        setattr(ctx.sufficiency, key, DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT))
    return ctx


def stock_results():
    facts: list[tuple[Literal["financial", "technical", "valuation"], str]] = [
        ("financial", "revenue"), ("technical", "latest_close"), ("valuation", "pb")]
    return [AnalysisResult(dimension=key, status="ok", score=8, summary="偏多",
                           metrics={metric: 12}) for key, metric in facts]


def test_default_pipeline_skips_scoring_and_both_model_stages(tmp_path, monkeypatch):
    config = Config(config_dir=tmp_path)
    registry = Registry()
    analyzer = RecordingAnalyzer()
    registry.register_analysis_module(analyzer)
    pipeline = Pipeline(registry, config, llm_enabled=True)
    ctx = stock_context(True)
    monkeypatch.setattr(pipeline, "collect", lambda *args, **kwargs: ctx)
    monkeypatch.setattr("data.enricher.ContextEnricher.enrich", lambda self, context: context)
    monkeypatch.setattr("data.peer_comparison.PeerComparisonService.enrich", lambda self, context, **kwargs: context)
    monkeypatch.setattr("data.financial_scoring_facts.FinancialScoringFactsService.enrich", lambda self, context, **kwargs: context)
    monkeypatch.setattr(pipeline, "_generate_commentary", lambda *a, **k: pytest.fail("关闭模式不得生成解读"))
    monkeypatch.setattr("data.enrichers.sentiment_enricher.SentimentEnricher.set_llm",
                        lambda *a: pytest.fail("关闭模式不得评估舆情"))
    results, commentary, context = pipeline.run(ctx.symbol, ctx.name)
    assert not context.extra_analysis_enabled
    assert analyzer.received is None
    assert commentary == {}
    assert results[0].score is None and results[0].risk_flags == []
    assert results[0].metrics["latest_close"] == 12


def test_objective_outputs_keep_facts_without_score_signal_or_fallback_judgment():
    ctx = stock_context(False)
    results = stock_results()
    payload = build_stock_payload(ctx.symbol, ctx.name, results, {"bulk": "模型判断"}, ctx,
                                  signal_cfg=SignalConfig())
    text = build_report(ctx.symbol, ctx.name, results, {"bulk": "模型判断"}, ctx,
                        signal_cfg=SignalConfig())
    assert payload["analysis_mode"] == "objective"
    assert payload["score"]["final"] is None and payload["signal"] is None
    assert payload["commentary_status"] == "not_requested" and payload["commentary"] == ""
    assert payload["dimensions"]["financial"]["metrics"]["revenue"] == 12
    assert "客观数据模式" in text
    for forbidden in ("综合评分", "综合研判", "模型信号", "模型判断", "偏多"):
        assert forbidden not in text
    assert payload["data_quality"]["has_analysis_data"]
    assert payload["data_quality"]["coverage"] > 0
    from push.summary import build_stock_summary

    pushed = build_stock_summary(ctx.symbol, ctx.name, results, ctx, SignalConfig())
    assert "客观数据模式" in pushed and "偏多" not in pushed
    assert "综合得分" not in pushed and "操作信号" not in pushed


def test_hk_enabled_signal_requires_all_three_core_dimensions():
    ctx = stock_context(True, "hk")
    results = stock_results()
    quality = build_data_quality(ctx, results)
    assert quality["signal_allowed"]
    payload = build_stock_payload(ctx.symbol, ctx.name, results, {}, ctx, signal_cfg=SignalConfig())
    assert payload["signal"]["level"] == "attack"
    ctx.sufficiency.valuation = DimensionSufficiency(level=SufficiencyLevel.PARTIAL)
    assert not build_data_quality(ctx, results)["signal_allowed"]
    assert "估值" in build_data_quality(ctx, results)["signal_unavailable_reason"]


def test_index_default_preserves_metrics_without_rules_or_commentary(tmp_path, monkeypatch):
    target = AnalysisTarget(target_type="index", symbol="000300", name="沪深300",
                            market="a-shares", index_style="broad")
    context = IndexAnalysisContext(target=target)
    context.risk_flags = ["同日基准行情不可用，不能计算超额收益"]
    pipeline = IndexPipeline(config=Config(config_dir=tmp_path), llm_enabled=True)
    monkeypatch.setattr(pipeline._collector, "collect", lambda *a, **k: context)
    monkeypatch.setattr("index.commentary.apply_commentary", lambda *a: pytest.fail("客观模式不执行解读"))
    report = pipeline.run([target]).reports[0]
    assert report.analysis_mode == "objective"
    assert report.commentary == "" and report.tag_technical is None
    text = render_index_report_markdown(report)
    assert "客观数据模式" in text and "综合研判" not in text
    assert "同日基准行情不可用" in text


def test_objective_preserves_actual_missing_reason_and_hk_industry_scope():
    from analysis.mode import objective_results
    from report.quality import hk_industry_label

    ctx = stock_context(False, "hk")
    results = objective_results([AnalysisResult(dimension="industry", status="unavailable",
        summary="非港股通标的，不参与港股行业对比", metrics={})])
    assert "非港股通" in results[0].summary
    assert hk_industry_label(ctx, results) == "非港股通（不支持行业对比）"
    quality = build_data_quality(ctx, results)
    assert any("非港股通" in item["value"] for item in quality["limitations"])


def test_complete_objective_data_is_not_degraded_by_absent_scores():
    from analysis.mode import objective_results

    ctx = stock_context(False)
    for key in ("industry", "sentiment"):
        setattr(ctx.sufficiency, key, DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT))
    results = stock_results() + [
        AnalysisResult(dimension="industry", status="ok", summary="同行", metrics={"peer_count": 5}),
        AnalysisResult(dimension="sentiment", status="ok", summary="新闻", metrics={"headlines": ["新闻"]}),
    ]
    quality = build_data_quality(ctx, objective_results(results))
    assert quality["coverage"] == 100 and quality["status"] == "ok"
    assert not quality["has_scored_data"]


def test_objective_news_markdown_retains_real_date_and_all_sources():
    ctx = stock_context(False)
    news = AnalysisResult(dimension="sentiment", status="ok", summary="", metrics={"news_items": [{
        "title": "公告 & 原文", "publish_date": "2026-10-30", "links": [
            {"media": "原始媒体", "url": "https://example.com/news"},
            {"media": "公告来源", "url": "https://example.com/notice"}]}]})
    text = build_report(ctx.symbol, ctx.name, stock_results() + [news], {}, ctx)
    assert "2026&#45;10&#45;30" in text and "原始媒体" in text and "公告来源" in text
    assert "https://example.com/news" in text and "https://example.com/notice" in text
    assert "舆情原风险分" not in text and "标注状态" not in text


def test_objective_news_preserves_collection_limit_and_disclaimer():
    from analysis.mode import objective_results

    ctx = stock_context(False, "hk")
    result = AnalysisResult(dimension="sentiment", status="ok", summary="已取得新闻", metrics={
        "news_items": [{"title": "实际候选", "published_at": "2026-10-30"}],
        "news_disclaimer": "关键词检索结果，不保证完整覆盖近30天新闻。",
        "news_notes": ["已达到单关键词100条候选上限，不保证覆盖窗口内全部新闻。"],
    })
    payload = build_stock_payload(ctx.symbol, ctx.name, objective_results([result]), {}, ctx)
    assert payload["dimensions"]["sentiment"]["metrics"]["news_notes"] == result.metrics["news_notes"]
    text = build_report(ctx.symbol, ctx.name, [result], {}, ctx)
    assert "单关键词100条候选上限" in text and "不保证完整覆盖近30天新闻" in text


@pytest.mark.parametrize("original,current,model", [(False, True, True), (True, False, True), (True, True, False)])
def test_stock_retry_cannot_bypass_original_or_current_policy(tmp_path, monkeypatch, original, current, model):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from api.app import create_app
    from report.commentary import CommentaryContext, commentary_store

    monkeypatch.chdir(tmp_path)
    ctx = stock_context(original)
    token = commentary_store.remember(CommentaryContext(ctx.symbol, ctx.name, stock_results(), {}, ctx, None))
    pipeline = SimpleNamespace(_extra_analysis_enabled=current, _llm_enabled=model,
                               _generate_commentary=lambda *a, **k: pytest.fail("策略拒绝时不得调用模型"))
    client = TestClient(create_app(core=SimpleNamespace(pipeline=pipeline, llm=object()), push=False))
    response = client.post("/api/v1/commentary/retry", json={"report_token": token})
    assert response.status_code == 403


def test_objective_news_coverage_does_not_wait_for_model_assessment():
    ctx = stock_context(False)
    result = AnalysisResult(dimension="sentiment", status="ok", summary="取得新闻",
                            metrics={"news_items": [{"title": "原始新闻"}]})
    quality = build_data_quality(ctx, [result])
    news = next(item for item in quality["dimensions"] if item["key"] == "sentiment")
    assert news["status"] == "ok"
    assert all(item["label"] != "有效评估" for item in news["fields"])


@pytest.mark.parametrize("available,allowed", [(10, True), (7, False)])
def test_hk_real_financial_enricher_uses_verified_scoring_basis_for_signal(available, allowed):
    from datetime import date

    from data.enrichers.financial_enricher import FinancialEnricher
    from data.schemas import FinancialData

    ctx = stock_context(True, "hk")
    ctx.financial_data = [FinancialData(symbol=ctx.symbol, fiscal_quarter=date(2026, 6, 30), revenue=100)]
    ctx = FinancialEnricher().enrich(ctx)
    assert ctx.sufficiency.financial.level == SufficiencyLevel.PARTIAL
    results = stock_results()
    results[0].metrics["scoring_basis"] = {"available_max": available, "eligible_max": 10}
    results[0].status = "ok" if allowed else "partial"
    quality = build_data_quality(ctx, results)
    assert quality["signal_allowed"] is allowed


@pytest.mark.parametrize("original,current", [(False, True), (True, False)])
def test_index_retry_respects_original_and_current_mode(tmp_path, monkeypatch, original, current):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from api.app import create_app
    from index.build_single import IndexReportBuilder
    from index.commentary import commentary_store

    monkeypatch.chdir(tmp_path)
    context = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300",
                                    name="沪深300", market="a-shares", index_style="broad"),
                                    extra_analysis_enabled=original)
    report = IndexReportBuilder().build(context, [])
    token = commentary_store.remember(report, "{}")
    core = SimpleNamespace(index_pipeline=SimpleNamespace(_extra_analysis_enabled=current, _llm_enabled=True),
                           llm=SimpleNamespace(generate=lambda *a: pytest.fail("模式关闭不得调用模型")))
    client = TestClient(create_app(core=core, push=False))
    response = client.post("/api/v1/index/commentary/retry", json={"report_token": token})
    assert response.status_code == 403

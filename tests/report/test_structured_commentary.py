"""结构化解读规则与模型边界，不使用理想化分数代替事实。"""
import json

import pytest

from data.schemas import AnalysisContext, AnalysisResult
from report.research import build_research, model_prompt, parse_model, render_research


def result(dim, **metrics):
    return AnalysisResult(dimension=dim, status="ok", summary="", metrics=metrics)


def test_technical_disagreement_is_not_a_crossover_or_volume_decline():
    ctx = AnalysisContext(symbol="601899", name="紫金矿业")
    report = build_research(ctx, [result("technical", latest_close=28.28, ma_5=28.26,
        ma_20=28.72, ma_60=31.32, macd_dif=-0.677, macd_dea=-0.858, volume_ratio=1.63)])
    text = report["dimensions"][1]["text"]
    assert "分歧" in text
    assert "金叉" not in text and "放量下跌" not in text


def test_unavailable_and_invalid_valuation_never_reuse_percentile():
    ctx = AnalysisContext(symbol="000338", name="潍柴动力")
    report = build_research(ctx, [result("valuation", pe_ttm=-3, pe_percentile=0,
        valuation_applicability={"pe_ttm": {"applicable": False}})])
    assert "低位" not in report["dimensions"][2]["text"]
    assert "不足" in report["dimensions"][4]["text"]
    unavailable = AnalysisResult(dimension="valuation", status="unavailable", summary="不可用")
    peers = result("industry", comparison={"valuation": {"metrics": [
        {"key": "pe_ttm", "target": 12, "sample_count": 10, "percentile": 1}]}})
    report = build_research(ctx, [unavailable, peers])
    assert "不足" in report["dimensions"][2]["text"]


def test_same_period_peer_axes_keep_divergence():
    comparison = {"periods": {"latest": {"label": "前三季度", "metrics": [
        {"key": key, "percentile": pct, "sample_count": 15}
        for key, pct in [("roe", 94), ("net_margin", 100), ("revenue_growth_yoy", 6)]]}}}
    ctx = AnalysisContext(symbol="601899", name="紫金矿业")
    report = build_research(ctx, [result("industry", comparison=comparison)])
    text = report["dimensions"][3]["text"]
    assert "较高" in text and "较低" in text and "前三季度" in text


def test_structured_model_references_and_fallback():
    ctx = AnalysisContext(symbol="000338", name="潍柴动力")
    research = build_research(ctx, [result("financial", net_profit=-10)])
    raw = json.dumps({"business": "", "conclusion": {"overall": "存在亏损。", "support": "支撑证据不足。",
        "question": "盈利恢复仍不确定。"}, "evidence_ids": ["financial"], "risks": []}, ensure_ascii=False)
    parsed = parse_model(raw, research)
    assert parsed["conclusion"]["overall"] == "存在亏损。"
    assert "五维解读" in render_research(research)
    assert "整体判断" not in render_research(research)
    assert "不重新计算分数" in model_prompt(ctx, research)
    with pytest.raises(ValueError):
        parse_model(raw.replace('"financial"', '"invented"'), research)
    with pytest.raises(ValueError):
        parse_model("普通长文章", research)


def test_legacy_hk_or_objective_is_not_migrated():
    from report.payload import build_stock_payload

    for ctx in [AnalysisContext(symbol="00700.HK", name="腾讯", market="hk"),
                AnalysisContext(symbol="000338", name="潍柴动力", extra_analysis_enabled=False)]:
        payload = build_stock_payload(ctx.symbol, ctx.name, [], {}, ctx)
        assert not payload.get("research_commentary")


@pytest.mark.parametrize("market", ["a-shares", "hk", "us"])
@pytest.mark.parametrize("raw", ["valid", "非JSON长文", "（LLM 分析暂时不可用：超时）"])
def test_pipeline_single_call_and_shared_output(tmp_path, raw, market):
    from core.pipeline import Pipeline
    from core.registry import Registry
    from llm.base import LLMBackend
    from report.payload import build_stock_payload
    from report.scoring import build_report
    from utils.config import Config

    class Backend(LLMBackend):
        calls = 0

        @property
        def model_name(self):
            return "test"

        def generate(self, prompt, **kwargs):
            self.calls += 1
            assert "五维短句已由程序确定" in prompt
            return json.dumps({"business": "", "conclusion": {"overall": "利润承压。",
                "support": "支撑证据不足。", "question": "盈利恢复尚待验证。"},
                "evidence_ids": ["financial"], "risks": [{"id": "financial.loss", "text": "亏损可能影响经营积累。"}]}, ensure_ascii=False) if raw == "valid" else raw

    ctx = AnalysisContext(symbol="000338", name="潍柴动力", market=market, commentary_version=2)
    results = [result("financial", net_profit=-10)]
    registry = Registry()
    backend = Backend()
    registry.register_llm_backend(backend, provider="openai")
    config = Config(config_dir=tmp_path)
    config.data["analysis"]["extra_enabled"] = True
    comments = Pipeline(registry, config)._generate_commentary(ctx.symbol, ctx.name, results, context=ctx)
    assert backend.calls == 1
    payload = build_stock_payload(ctx.symbol, ctx.name, results, comments, ctx)
    markdown = build_report(ctx.symbol, ctx.name, results, comments, ctx)
    assert "五维解读" in markdown
    assert payload["research_commentary"]["dimensions"][0]["text"] in markdown
    assert payload["commentary_status"] == ("ok" if raw == "valid" else "failed")
    assert ("整体判断" in markdown) == (raw == "valid")


def test_cash_support_uses_period_and_source_evidence():
    from datetime import date

    from data.schemas import FinancialData

    row = FinancialData(symbol="000338", fiscal_quarter=date(2026, 9, 30),
        net_profit=10, operating_cash_flow=15, period_start=date(2026, 1, 1),
        cash_flow_period_start=date(2026, 1, 1), period_basis="cumulative", reporting_basis="合并",
        report_currency="CNY", cash_flow_basis="reported", financial_source="同源")
    ctx = AnalysisContext(symbol="000338", name="样本", financial_data=[row])
    results = [result("financial", net_profit=10)]
    assert "形成支持" in build_research(ctx, results)["dimensions"][0]["text"]
    ctx.financial_data = [row.model_copy(update={"cash_flow_period_start": None})]
    assert "形成支持" not in build_research(ctx, results)["dimensions"][0]["text"]
    ctx.financial_data = [row]
    unavailable = AnalysisResult(dimension="financial", status="unavailable", summary="缺失")
    assert "形成支持" not in build_research(ctx, [unavailable])["dimensions"][0]["text"]


@pytest.mark.parametrize("text", ["净利润同比999%。", "ROE888.88%。", "利润9。"])
def test_chinese_adjacent_or_substring_numbers_cannot_bypass(text):
    research = build_research(AnalysisContext(symbol="000338", name="样本"), [result("financial", net_profit=99)])
    raw = json.dumps({"business": "", "conclusion": {"overall": text, "support": "证据有限。", "question": "尚不明确。"},
        "evidence_ids": ["financial"], "risks": []}, ensure_ascii=False)
    with pytest.raises(ValueError):
        parse_model(raw, research)


def test_insufficient_keeps_same_business_and_five_rows_in_export():
    from report.payload import build_stock_payload
    from report.scoring import build_report

    ctx = AnalysisContext(symbol="000338", name="样本", commentary_version=2,
                          business_profile={"main_business": "业务" * 100})
    payload = build_stock_payload(ctx.symbol, ctx.name, [], {}, ctx)
    markdown = build_report(ctx.symbol, ctx.name, [], {}, ctx)
    assert payload["commentary_status"] == "insufficient"
    assert payload["research_commentary"]["business_text"] in markdown
    assert "五维解读" in markdown and "整体判断" not in markdown


@pytest.mark.parametrize(("market", "symbol"), [("hk", "00700.HK"), ("us", "AAPL")])
def test_overseas_v2_offline_outputs(market, symbol):
    from report.payload import build_stock_payload
    from report.scoring import build_report

    ctx = AnalysisContext(
        symbol=symbol, name="样本", market=market, commentary_version=2,
        business_profile={"main_business": "主营公司业务。", "source": "F10", "source_label": "东财 F10 公司资料"},
    )
    results = [result("financial", net_profit=20)]
    payload = build_stock_payload(ctx.symbol, ctx.name, results, {}, ctx, no_llm=True)
    research = payload["research_commentary"]
    assert research["market"] == market and research["profile_version"] == 1
    assert len(research["dimensions"]) == 5
    markdown = build_report(ctx.symbol, ctx.name, results, {}, ctx, no_llm=True)
    assert "东财 F10 公司资料" in markdown and "五维解读" in markdown
    assert "核心结论" not in markdown
    prompt = model_prompt(ctx, research)
    if market == "hk":
        assert "HSICS" in prompt and "报告期" in prompt and "币种未核实" in prompt
    else:
        assert all(term not in prompt for term in ("GICS", "申万", "HSICS", "FCF"))
        assert "TTM" in prompt and "年度" in prompt and "舆情本期未采集" in prompt
        assert research["dimensions"][-1]["status"] == "not_collected"
        assert "不能视为零风险" in research["dimensions"][-1]["text"]
        assert "本期未采集可评估舆情样本" in markdown.split("数据覆盖与时效", 1)[1]
        assert research["risk_candidates"] == []


def test_formatted_thousands_normalize_without_accepting_changed_units():
    research = build_research(AnalysisContext(symbol="AAPL", name="样本", market="us"), [
        result("financial", revenue=396431000000),
    ])
    def model(overall):
        return json.dumps({"business": "", "conclusion": {"overall": overall,
                           "support": "依据有限。", "question": "需要复核。"},
                           "evidence_ids": ["financial"], "risks": []}, ensure_ascii=False)
    parse_model(model("营收3964.31 亿。"), research)
    for text in ("营收3964.31 万。", "营收3964.31%。", "营收964.31 亿。"):
        with pytest.raises(ValueError):
            parse_model(model(text), research)

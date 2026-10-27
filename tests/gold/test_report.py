"""黄金观察简报的首屏、缺失数据和文本安全。"""

from gold.report import render_gold_markdown


def sample():
    market = {
        "status": "ok", "unit": "元/克", "source": "上海黄金交易所",
        "as_of": "2026-10-26", "latest_close": 861.41, "daily_change_pct": -0.3,
        "performance": {"20": {"return_pct": 2.3, "start": "2026-09-20", "end": "2026-10-26", "count": 21}},
        "short_term": {"direction": "偏空"},
        "indicators": {"ma20": 870, "ma60": 850, "rsi14": 29, "atr14": 10},
        "risks": [{"key": "oversold", "active": True, "value": 29, "threshold": 30}],
    }
    overseas = {**market, "unit": "美元/金衡盎司", "source": "新浪", "as_of": "2026-10-23", "latest_close": 3997.79, "short_term": {"direction": "中性"}, "risks": []}
    return {
        "report_schema": "gold-observation-v2", "generated_at": "2026-10-27T12:00:00+08:00",
        "markets": {"domestic": market, "international": overseas},
        "observations": [{"name": "实际利率", "state": "ok", "as_of": "2026-10-23", "source": "美国财政部", "main": {"window": 120, "change": -30, "unit": "bp"}}],
        "sources": [{"name": "美元指数", "published_at": "2026-10-19T00:00:00-04:00", "metadata": {"published_precision": "date"}}],
    }


def test_price_first_and_observations_without_advice():
    text = render_gold_markdown(sample())
    assert text.startswith("# 黄金市场观察简报\n\n## 黄金价格")
    assert "**861.41 元/克**" in text
    assert "**3,997.79 美元/金衡盎司**" in text
    assert "2026-10-26" in text and "2026-10-23" in text
    assert "快速快照" in text and "偏空" in text and "超卖" in text
    assert text.index("## 黄金价格") < text.index("## 近期价格变化") < text.index("## 辅助分析")
    assert "实际利率" in text and "-30" in text
    assert "2026-10-19（来源当地日期）" in text
    for forbidden in ("加权合计", "长期看多", "长期配置建议", "短期交易参考", "分批买入", "预览"):
        assert forbidden not in text


def test_missing_and_stale_prices_are_not_current():
    data = sample()
    data["markets"]["domestic"]["status"] = "stale"
    data["markets"]["international"] = {"status": "unavailable"}
    text = render_gold_markdown(data)
    assert "旧缓存/过期" in text and "不可用" in text
    assert "无法判断" in text and "数据缺失" in text
    assert "**0" not in text


def test_partial_and_historical_references_are_labeled_in_report():
    data = sample()
    data["markets"]["domestic"]["result_state"] = "partial"
    data["observations"][0].update({"source_status": "stale", "state": "数据缺失", "latest_value": 2.66})
    data["sources"].append({"name": "国内黄金", "status": "ok", "collection_record": {"result_state": "partial"}})
    text = render_gold_markdown(data)
    assert "正常（部分数据）" in text
    assert "历史参考值 2.66" in text
    assert "观察日期 2026-10-23" in text
    assert "最新值 2.66" not in text


def test_partial_macro_is_labeled_without_hiding_current_observation():
    data = sample()
    data["observations"][0].update({"source_status": "ok", "result_state": "partial", "state": "有效"})
    text = render_gold_markdown(data)
    assert "状态 有效（部分数据）" in text


def test_untrusted_text_is_escaped_and_ai_text_omitted():
    data = sample()
    data["observations"][0]["name"] = "<script>alert(1)</script>|# hi"
    data["commentary"] = "必须立刻买入"
    text = render_gold_markdown(data)
    assert "<script>" not in text
    assert "&lt;script&gt;" in text and "&#124;" in text
    assert "必须立刻买入" not in text


def test_empty_input_and_nonfinite_values_are_missing():
    data = sample()
    data["markets"]["domestic"]["latest_close"] = float("nan")
    assert "nan" not in render_gold_markdown(data).lower()
    assert "数据缺失" in render_gold_markdown({})


def test_monthly_window_and_reverse_percentile_are_explained():
    data = sample()
    data["observations"].append({"key": "china_reserves", "name": "中国储备", "main": {"window": 6, "change": 2}, "marginal": {"window": 3, "change": 1}})
    text = render_gold_markdown(data)
    assert "6 个月变化" in text and "3 个月变化" in text
    assert "实际利率与美元变化的分位按变化取反计算" in text
    assert "国内金条、国内黄金 ETF 参考国内价格" in text

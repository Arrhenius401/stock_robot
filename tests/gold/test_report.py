"""黄金报告的事实表达与不可信文本隔离回归。"""

from copy import deepcopy

import markdown
import pytest

from gold.report import render_gold_markdown


def test_summary_precedes_long_factor_tables_and_date_precision(payload):
    payload['sources'] = [{'key': 'dollar', 'published_at': '2026-10-19T00:00:00-04:00',
                           'metadata': {'published_precision': 'date'}}]
    payload['commentary_status'] = 'unavailable'
    text = render_gold_markdown(payload)
    assert text.index('## 结论摘要') < text.index('## 长期方向与证据')
    assert '2026-10-19（来源当地日期）' in text
    assert '2026-10-19T00:00' not in text
    assert 'AI 未配置或不可用' in text


@pytest.fixture
def payload() -> dict:
    factor = {
        "key": "real_yield",
        "name": "美国10年期实际收益率",
        "role": "核心",
        "weight": 2,
        "source": "美国财政部",
        "source_url": "https://home.treasury.gov/data",
        "unit": "%",
        "as_of": "2026-10-23",
        "published_at": None,
        "state": "ok",
        "main": {
            "change": -30,
            "start": "2026-04-20",
            "end": "2026-10-23",
            "count": 121,
            "signal": "利多",
            "significance": "显著利多",
            "percentile": 82.5,
            "percentile_status": "ok",
            "rank_start": "2021-10-23",
            "rank_end": "2026-10-22",
            "rank_count": 1100,
            "unit": "bp",
            "window": 120,
        },
        "marginal": {
            "change": -27,
            "start": "2026-07-20",
            "end": "2026-10-23",
            "count": 61,
            "signal": "利多",
            "percentile": None,
            "percentile_status": "历史不足",
            "unit": "bp",
            "window": 60,
        },
    }
    market = {
        "name": "国内黄金",
        "symbol": "Au99.99",
        "unit": "人民币元/克",
        "status": "ok",
        "source": "上海黄金交易所",
        "source_url": "https://www.sge.com.cn/",
        "as_of": "2026-10-23",
        "latest_close": 1000,
        "daily_change_pct": 0.8,
        "performance": {
            "20": {
                "return_pct": 6.2,
                "start": "2026-09-20",
                "end": "2026-10-23",
                "count": 21,
                "status": "ok",
            }
        },
        "short_term": {
            "direction": "看多（强）",
            "reason": "均线向上",
            "confidence": "中",
            "confidence_reasons": ["五年历史不足"],
        },
        "indicators": {
            "ma20": 980,
            "ma60": 950,
            "slope_pct": 1.2,
            "rsi14": 72,
            "atr14": 15,
            "vol20_pct": 18,
            "vol_percentile": 82,
            "max_drawdown_pct": -12,
            "annual_vol_pct": 20,
        },
        "price_position": {
            "3y": {
                "percentile": 90,
                "start": "2023-10-23",
                "end": "2026-10-23",
                "count": 750,
                "status": "ok",
            },
            "5y": {"percentile": None, "status": "历史不足"},
        },
        "risks": [
            {
                "key": "chase",
                "message": "追高风险高",
                "value": 72,
                "threshold": 70,
                "active": True,
            }
        ],
        "reassessment": [
            {
                "key": "chase",
                "description": "解除追高警报后复核趋势",
                "value": {"RSI": 72, "偏离ATR": 1.4},
                "threshold": {"RSI上限": 65, "偏离ATR上限": 1},
                "met": False,
            }
        ],
        "allocation": {
            "label": "当前不追涨",
            "reason": "长期看多但追高警报未解除",
            "conditions": ["RSI≤65且偏离≤1ATR"],
            "batch_guidance": None,
        },
        "trading": {
            "label": "等待风险解除",
            "reason": "追高警报未解除",
            "scope": "仅为技术面参考",
            "conditions": ["复核强看多"],
        },
    }
    international = deepcopy(market)
    international.update(
        name="国际黄金", symbol="XAU", unit="美元/金衡盎司", source="新浪"
    )
    international["short_term"]["direction"] = "偏空"
    international["allocation"]["label"] = "国际暂不新增"
    international["trading"]["label"] = "国际观望"
    return {
        "report_type": "gold",
        "symbol": "GOLD",
        "name": "黄金综合研报",
        "generated_at": "2026-10-26T12:00:00+08:00",
        "rules_version": "gold-v1",
        "reading_guide": "先看方向与风险，再按持有期限阅读国内或国际的两套指引。",
        "long_term": {
            "direction": "看多",
            "reason": "核心双窗口同向",
            "confidence": "中",
            "confidence_reasons": ["官方储备仅中国样本"],
            "missing": ["全球官方储备"],
            "windows": {
                "main": {
                    "window": 120,
                    "direction": "看多",
                    "core_score": 4,
                    "reference_score": 1,
                    "total_score": 5,
                },
                "marginal": {
                    "window": 60,
                    "direction": "看多",
                    "core_score": 4,
                    "reference_score": 0,
                    "total_score": 4,
                },
            },
            "factors": [factor],
        },
        "markets": {"domestic": market, "international": international},
        "fx_decomposition": [
            {
                "window": 20,
                "start": "2026-09-20",
                "end": "2026-10-16",
                "count": 21,
                "international_return_pct": 10,
                "fx_return_pct": 2,
                "domestic_return_pct": 13,
                "interaction_pp": 0.2,
                "reference_return_pct": 12.2,
                "international_contribution_pp": 10.1,
                "fx_contribution_pp": 2.1,
                "residual_pp": 0.8,
            }
        ],
        "sources": [
            {
                "key": "holdings",
                "name": "北美 ETF 资金样本（SPDR）",
                "source": "SPDR",
                "source_url": "https://www.spdrgoldshares.com/usa/gld/",
                "as_of": "2026-10-22",
                "published_at": None,
                "unit": "吨",
                "status": "stale",
                "notes": ["旧缓存，刷新失败"],
            }
        ],
        "notes": ["全球储备未通过接入验收"],
        "commentary": None,
        "commentary_status": "disabled",
    }


def test_report_preserves_directions_separate_guidance_and_evidence(payload):
    report = render_gold_markdown(payload)
    for text in (
        "长期（1—3 年）：看多",
        "短期（几周至3个月）：看多（强）",
        "短期（几周至3个月）：偏空",
        "国内黄金",
        "国际黄金",
        "长期配置建议",
        "短期交易参考",
        "当前不追涨",
        "国际暂不新增",
        "国际观望",
        "证据支撑度：中",
        "官方储备仅中国样本",
        "规则版本：gold-v1",
        "未经过预测有效性验证",
    ):
        assert text in report
    assert report.index("国内黄金") < report.index("国际暂不新增")
    assert "仅为技术面参考，不构成长期配置依据" in report
    assert "依据的证据支撑度：长期中；本市场短期中" in report
    assert "本市场短期证据支撑度：中" in report


def test_report_exposes_windows_positions_dates_units_and_quantified_thresholds(
    payload,
):
    report = render_gold_markdown(payload)
    for text in (
        "120",
        "60",
        "核心",
        "参考",
        "-30",
        "-27",
        "82.5%",
        "bp",
        "2026-04-20",
        "2026-07-20",
        "2026-10-23",
        "人民币元/克",
        "美元/金衡盎司",
        "3年",
        "5年",
        "90%",
        "历史不足",
        "RSI上限：65",
        "偏离ATR上限：1",
        "未满足",
        "追高风险高",
        "美国财政部",
        "发布日期未记录",
    ):
        assert text in report
    assert "赔率" not in report
    assert "高估" not in report
    assert "低估" not in report


def test_report_keeps_fx_interaction_residual_and_reference_identity(payload):
    report = render_gold_markdown(payload)
    for text in (
        "近20个共同观察日",
        "2026-10-16",
        "交互项",
        "0.2",
        "10.1",
        "2.1",
        "0.8",
        "百分比点",
        "未解释差额",
        "非同步日线参考比较",
        "不是因果归因",
        "纽约中午",
        "CFD",
        "北美",
        "不代表全球",
    ):
        assert text in report
    assert "旧缓存，刷新失败" in report


def test_rank_evidence_includes_significance_and_actual_volatility_sample(payload):
    payload["markets"]["domestic"]["indicators"].update(
        vol_rank_start="2024-01-02",
        vol_rank_end="2026-10-22",
        vol_rank_count=650,
        vol_rank_period="可用历史（不足五年）",
    )
    report = render_gold_markdown(payload)
    for text in (
        "显著利多",
        "2021-10-23",
        "1100",
        "可用历史（不足五年）",
        "2024-01-02",
        "650",
    ):
        assert text in report


@pytest.mark.parametrize(
    "direction", ["看多（强）", "偏多", "中性", "偏空", "看空（强）"]
)
def test_report_preserves_each_short_direction_without_reinterpreting(
    payload, direction
):
    payload["markets"]["domestic"]["short_term"]["direction"] = direction
    payload["long_term"]["direction"] = "看空"
    report = render_gold_markdown(payload)
    assert f"短期（几周至3个月）：{direction}" in report
    assert "长期（1—3 年）：看空" in report


@pytest.mark.parametrize(
    "status, expected",
    [("disabled", "已禁用"), ("failed", "解释失败"), ("ok", "辅助解释")],
)
def test_ai_status_never_removes_deterministic_result(payload, status, expected):
    payload["commentary_status"] = status
    payload["commentary"] = "解释文本" if status == "ok" else None
    report = render_gold_markdown(payload)
    assert expected in report
    assert "长期（1—3 年）：看多" in report
    assert "量化重估" in report


def test_missing_fields_stay_missing_not_zero_or_neutral():
    report = render_gold_markdown({})
    assert "无法判断" in report
    assert "数据缺失" in report
    assert "国内黄金" in report and "国际黄金" in report
    assert "未提供" in report
    assert "nan" not in report.lower()


def test_guide_is_at_most_one_hundred_visible_characters(payload):
    payload["reading_guide"] = "读" * 200
    report = render_gold_markdown(payload)
    guide = report.split("## 阅读指南\n\n", 1)[1].split("\n", 1)[0]
    assert len(guide) <= 100


def test_untrusted_text_cannot_inject_html_links_tables_or_headings(payload):
    attack = "<script>alert(1)</script>\n## 注入标题\n|伪造|\n[点我](javascript:alert(1)) ![图](https://evil.test/a) **强制建议**"
    payload["name"] = attack
    payload["notes"] = [attack]
    payload["commentary_status"] = "ok"
    payload["commentary"] = attack
    payload["long_term"]["factors"][0]["source"] = attack
    payload["markets"]["domestic"]["allocation"]["reason"] = attack
    payload["markets"]["domestic"]["reassessment"][0]["threshold"] = {attack: attack}
    rendered = markdown.markdown(render_gold_markdown(payload), extensions=["tables"])
    assert "<script>" not in rendered
    assert 'href="javascript:' not in rendered
    assert 'src="https://evil' not in rendered
    assert "<h2>注入标题</h2>" not in rendered
    assert "<strong>强制建议</strong>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_nonfinite_values_and_zeros_remain_honest(payload):
    payload["markets"]["domestic"]["latest_close"] = float("nan")
    payload["markets"]["domestic"]["daily_change_pct"] = float("inf")
    payload["markets"]["domestic"]["indicators"]["slope_pct"] = 0
    report = render_gold_markdown(payload)
    assert "收盘：数据缺失" in report
    assert "日涨跌：数据缺失" in report
    assert "| MA60二十日斜率 | 0 | % |" in report
    assert "nan" not in report.lower()
    assert "inf" not in report.lower()


@pytest.mark.parametrize("text", ["1. 注入列表", "- 注入列表", "+ 注入列表", "---"])
def test_standalone_upstream_text_cannot_become_list_or_separator(text):
    report = render_gold_markdown(
        {"reading_guide": text, "commentary_status": "ok", "commentary": text}
    )
    rendered = markdown.markdown(report, extensions=["tables"])
    assert "<ol>" not in rendered
    assert "<ul>" not in rendered
    assert "<hr" not in rendered


def test_reassessment_uses_chinese_labels_instead_of_internal_keys(payload):
    payload['markets']['domestic']['allocation']['conditions'] = [{'key': 'heat_exit',
        'description': '过热解除', 'threshold': {'rsi_lte': 65, 'atr_lte': 1}, 'met': False}]
    report = render_gold_markdown(payload)
    assert 'RSI不高于：65' in report
    assert 'heat_exit' not in report
    assert 'rsi_lte' not in report

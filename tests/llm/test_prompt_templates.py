from pathlib import Path
import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src" / "llm" / "prompt_templates"

# 每个维度对应其 analyzer 真实产出的 metrics
METRICS = {
    "financial": {
        "latest_quarter": "2025-12-31", "revenue": 3.54e8, "net_profit": 7.2e7,
        "roe": 0.18, "revenue_growth_yoy": 0.12, "profit_growth_yoy": -0.05,
        "roe_trend": [{"quarter": "2025-12-31", "roe": 0.18}],
    },
    "technical": {
        "ma5": 10.5, "ma10": 10.2, "ma20": 10.0, "ma60": 9.8,
        "latest_close": 10.7, "price_vs_ma20": 7.0, "avg_volume_5d": 1000000,
        "volume_ratio": 1.3, "macd_dif": 0.12, "macd_dea": 0.08, "macd_bar": 0.08,
    },
    "valuation": {"pe_ttm": 7.5, "pb": 0.85, "ps_ttm": 1.2, "pe_percentile": 30.0},
    "industry": {"industry": "高速公路", "sector": "交通运输", "peers": ["600377", "600020"]},
    "sentiment": {"headline_count": 3, "headlines": ["利好A", "中性B", "利空C"], "date": "2026-07-08"},
}


def _env():
    return Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))


@pytest.mark.parametrize("provider", ["openai", "claude"])
@pytest.mark.parametrize("dimension", list(METRICS))
def test_template_renders_with_real_metrics(dimension, provider):
    template = _env().get_template(f"{dimension}_{provider}.jinja2")
    out = template.render(name="山东高速", symbol="600350", **METRICS[dimension])
    assert out.strip()  # 非空、无异常


@pytest.mark.parametrize("provider", ["openai", "claude"])
@pytest.mark.parametrize("dimension", list(METRICS))
def test_template_renders_when_numeric_metrics_none(dimension, provider):
    # 所有标量指标为 None（列表/字符串保留）也不应抛异常
    metrics = {
        k: (None if isinstance(v, (int, float)) else v)
        for k, v in METRICS[dimension].items()
    }
    template = _env().get_template(f"{dimension}_{provider}.jinja2")
    out = template.render(name="山东高速", symbol="600350", **metrics)
    assert out.strip()

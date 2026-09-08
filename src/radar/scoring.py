"""ETF 配置雷达的可回放日线评分。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import pandas as pd

from radar.models import RadarScoreProfile

REQUIRED_DAYS = 61
WEIGHTS = {"trend": 0.35, "drawdown": 0.25, "volatility": 0.20, "liquidity": 0.20}


def score_etfs(
    histories: Mapping[str, pd.DataFrame],
    categories: Mapping[str, str],
    weights: Mapping[str, float] = WEIGHTS,
    profile: RadarScoreProfile | None = None,
) -> pd.DataFrame:
    """按类别计算 ETF 分位评分；不足样本或历史时不生成伪排名。"""
    rows: list[dict[str, object]] = []
    for symbol, history in histories.items():
        factor = _factors(history, profile)
        rows.append({"symbol": symbol, "category": categories[symbol], **factor})
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["score"] = None
    result["rank"] = None
    result["grade"] = "unavailable"
    for _, group in result.groupby("category"):
        eligible = group.dropna(subset=list(weights))
        if len(eligible) < (profile.minimum_category_size if profile else 3):
            continue
        percentile = pd.DataFrame(index=eligible.index)
        percentile["trend"] = eligible["trend"].rank(pct=True)
        percentile["drawdown"] = eligible["drawdown"].rank(pct=True)
        percentile["volatility"] = eligible["volatility"].rank(pct=True)
        percentile["liquidity"] = eligible["liquidity"].rank(pct=True)
        weight_total = sum(weights.values())
        for key, weight in weights.items():
            result.loc[eligible.index, f"{key}_percentile"] = percentile[key]
            result.loc[eligible.index, f"{key}_contribution"] = percentile[key] * weight / weight_total * 100
        total: Any = sum(percentile[key] * weight for key, weight in weights.items()) / weight_total * 100
        result.loc[eligible.index, "score"] = total
        result.loc[eligible.index, "rank"] = total.rank(ascending=False, method="min").astype(int)
        result.loc[eligible.index, "grade"] = total.map(_grade)
    return result


def _factors(history: pd.DataFrame, profile: RadarScoreProfile | None = None) -> dict[str, float | None]:
    """从截至当前交易日的数据计算全部必需因子。"""
    windows = profile.windows if profile else {"short_return_days": 20, "medium_return_days": 60, "drawdown_days": 60, "volatility_days": 20, "liquidity_days": 20}
    required_days = max(windows.values()) + 1
    if len(history) < required_days or not {"close", "amount"} <= set(history.columns):
        return {key: None for key in WEIGHTS}
    raw_close: Any = history["close"]
    raw_amount: Any = history["amount"]
    close: Any = cast(Any, pd.to_numeric(raw_close, errors="coerce")).dropna()
    amount: Any = cast(Any, pd.to_numeric(raw_amount, errors="coerce")).dropna()
    if len(close) < required_days or len(amount) < windows["liquidity_days"] or close.iloc[-(windows["medium_return_days"] + 1)] == 0:
        return {key: None for key in WEIGHTS}
    short_return = close.iloc[-1] / close.iloc[-(windows["short_return_days"] + 1)] - 1
    medium_return = close.iloc[-1] / close.iloc[-(windows["medium_return_days"] + 1)] - 1
    window = close.iloc[-windows["drawdown_days"]:]
    max_drawdown = (window / window.cummax() - 1).min()
    volatility = close.pct_change().iloc[-windows["volatility_days"]:].std() * (252**0.5)
    return {
        "trend": float((short_return + medium_return) / 2),
        "drawdown": float(-max_drawdown),
        "volatility": float(-volatility),
        "liquidity": float(amount.iloc[-windows["liquidity_days"]:].mean()),
    }


def _grade(score: float) -> str:
    """将有效评分映射成研究等级，非交易指令。"""
    if score >= 66.67:
        return "偏好"
    if score >= 33.33:
        return "观察"
    return "谨慎"

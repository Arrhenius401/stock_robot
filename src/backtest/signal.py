"""技术信号生成 — 仅使用截至当日的 OHLCV，杜绝未来函数。"""

from typing import Any

import pandas as pd

from analysis.scorers.general import GeneralScorer
from backtest.models import BacktestStrategy
from data.schemas import AnalysisContext, PriceData
from report.signal import derive_signal


def build_target_weights(
    prices: list[PriceData],
    strategy: BacktestStrategy,
    technical_config: dict[str, Any],
    global_const: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """逐日构造"截至当日"的分析上下文，输出信号日 → 目标仓位的密集表。

    第 i 日的信号只依赖 prices[:i+1]，任何未来数据不参与计算；
    "信号在第 i+1 日开盘成交"的时序由 runner 的订单簿负责。
    返回 DataFrame 列为 signal / technical_score / target_weight，
    索引为信号生成日（DatetimeIndex）。
    """
    scorer = GeneralScorer(
        technical_config, global_const if global_const is not None else {}
    )
    dates = [p.trade_date for p in prices]
    signals: list[str] = []
    scores: list[float] = []
    targets: list[float] = []
    for i, price in enumerate(prices):
        # 只传截至当日的数据：AnalysisContext 必填 symbol/name，回测不解析公司名
        ctx = AnalysisContext(
            symbol=price.symbol, name=price.symbol, price_data=prices[: i + 1]
        )
        score, _, _ = scorer.score_technical(ctx)
        signal = derive_signal(score, strategy.thresholds)
        signals.append(signal)
        scores.append(score)
        targets.append(strategy.target_positions[signal])

    return pd.DataFrame(
        {
            "signal": signals,
            "technical_score": scores,
            "target_weight": targets,
        },
        index=pd.DatetimeIndex(dates, name="signal_date"),
    )

"""回测仿真 — 次日开盘调仓、费用拆分与 VectorBT 净值计算。"""

import logging
import math
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import vectorbt as vbt

from analysis.config_loader import ConfigLoader
from backtest.data import (
    BacktestDataError,
    HistoricalPriceProvider,
    validate_price_history,
)
from backtest.models import (
    BacktestRequest,
    BacktestResult,
    BacktestStrategy,
    BenchmarkSpec,
)
from backtest.signal import build_target_weights
from backtest.strategy import StrategyConfigError, StrategyRepository
from data.schemas import PriceData
from utils.config import Config

logger = logging.getLogger(__name__)

# 年化交易日基数（A 股日线回测惯例）
TRADING_DAYS = 252

# 成本档必需的五项费率键（缺失/非法直接失败，避免 _calc_fees 裸 KeyError）
COST_KEYS = (
    "commission_rate",
    "minimum_commission",
    "stamp_duty_rate",
    "transfer_fee_rate",
    "slippage_rate",
)


class BacktestRunner:
    """单股回测运行器：数据 → 信号 → 订单簿 → VectorBT 仿真 → 指标。"""

    def __init__(
        self,
        provider: HistoricalPriceProvider,
        strategies: StrategyRepository,
        config: Config,
        config_loader: ConfigLoader | None = None,
    ):
        self._provider = provider
        self._strategies = strategies
        self._config = config
        # 打分配置加载器复用一次：load("") 取默认行业模板的完整配置
        self._config_loader = config_loader if config_loader is not None else ConfigLoader()

    def run(self, request: BacktestRequest) -> BacktestResult:
        if request.start_date > request.end_date:
            raise BacktestDataError(
                f"日期区间无效: start={request.start_date} 晚于 end={request.end_date}"
            )
        strategy = self._strategies.get(request.strategy_id)
        benchmark = self._resolve_benchmark(request.benchmark_id)
        costs = self._resolve_costs(strategy.cost_profile)

        # 取数区间：start 前推 warmup 个交易日（自然日双倍兜底），供信号预热
        fetch_start = request.start_date - timedelta(days=int(strategy.warmup_days * 2))
        prices = self._provider.fetch_stock(request.symbol, fetch_start, request.end_date)
        validate_price_history(prices, fetch_start, request.end_date)
        data_start, data_end = prices[0].trade_date, prices[-1].trade_date
        # 预热覆盖校验：行情首日至回测起点须足 warmup_days（次新股/上市晚则明确失败）
        self._validate_warmup_coverage(prices, strategy, request.start_date)

        # 技术信号：无未来函数，第 i 日信号仅用截至 i 日数据
        technical_config = self._config_loader.load("")
        weights = build_target_weights(
            prices, strategy, technical_config, self._config_loader.global_const
        )

        # 自有订单簿：成交明细可精确断言（VectorBT 仅用于净值曲线）
        trades, size, exec_prices, fee_arr = self._build_order_book(
            prices, weights, costs, request.initial_cash
        )

        # VectorBT 单资产组合仿真：调仓日 size 为 ±股数、其余 0，
        # price 为滑点后执行价、fixed_fees 为本笔费用总额（其余日期占位 0）
        idx = pd.DatetimeIndex([p.trade_date for p in prices], name="trade_date")
        pf: Any = vbt.Portfolio.from_orders(
            close=pd.Series([p.close for p in prices], index=idx),
            size=pd.Series(size, index=idx),
            price=pd.Series(exec_prices, index=idx),
            fixed_fees=pd.Series(fee_arr, index=idx),
            init_cash=request.initial_cash,
            cash_sharing=False,
        )
        value: pd.Series = pf.value()

        # 基准收盘序列前向对齐到净值日，从 1.0 起算；对齐后仍有缺口则终止
        bench_net = self._align_benchmark(benchmark, idx, data_start, data_end)

        # 净值曲线：策略/基准净值（1.0 起算）+ 目标仓位与当日信号
        equity = pd.DataFrame(
            {
                "策略净值": value / request.initial_cash,
                "基准净值": bench_net.values,
                "目标仓位": weights["target_weight"].values,
                "当日信号": weights["signal"].values,
            },
            index=idx,
        )

        metrics, warnings = self._compute_metrics(equity, trades, request)

        return BacktestResult(
            equity_curve=equity,
            trades=trades,
            metrics=metrics,
            warnings=warnings,
            request=request,
            strategy=strategy,
            benchmark=benchmark,
            costs=costs,
            data_start=data_start,
            data_end=data_end,
        )

    def _resolve_benchmark(self, benchmark_id: str) -> BenchmarkSpec:
        """从全局配置解析基准，以配置键作为 id 显式补全。"""
        raw = self._config.get("backtest.benchmarks", {})
        if not isinstance(raw, dict) or benchmark_id not in raw:
            raise StrategyConfigError(f"未配置基准: {benchmark_id}")
        item = raw[benchmark_id]
        if not isinstance(item, dict) or not item.get("symbol"):
            raise StrategyConfigError(f"基准配置无效: {benchmark_id}")
        return BenchmarkSpec(
            id=benchmark_id,
            name=str(item.get("name") or benchmark_id),
            symbol=str(item["symbol"]),
        )

    def _resolve_costs(self, cost_profile: str) -> dict[str, float]:
        """一次性校验成本档：目标档位存在且五项费率键齐全、值为非负数字。"""
        costs = self._config.get(f"backtest.cost_profiles.{cost_profile}")
        if not isinstance(costs, dict) or not costs:
            raise StrategyConfigError(f"成本配置不存在: {cost_profile}")
        missing = [key for key in COST_KEYS if key not in costs]
        if missing:
            raise StrategyConfigError(
                f"成本配置 {cost_profile} 缺少键: {', '.join(missing)}"
            )
        resolved: dict[str, float] = {}
        for key in COST_KEYS:
            value = costs[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise StrategyConfigError(
                    f"成本配置 {cost_profile} 的 {key} 必须是数字"
                )
            numeric = float(value)
            if numeric < 0:
                raise StrategyConfigError(
                    f"成本配置 {cost_profile} 的 {key} 必须为非负数"
                )
            resolved[key] = numeric
        return resolved

    @staticmethod
    def _validate_warmup_coverage(
        prices: list[PriceData], strategy: BacktestStrategy, start_date: date
    ) -> None:
        """校验预热覆盖：prices[0] 至 start_date 间的实际交易日数须 ≥ warmup_days。

        次新股/上市晚于取数起点的股票，正式区间前段信号会基于截断历史计算，
        指标失真，须明确失败并给出原因。prices[0] 可能晚于取数起点
        （数据源从上市日起返回），校验以实际行情首日为准。
        """
        coverage = sum(1 for p in prices if p.trade_date < start_date)
        if coverage < strategy.warmup_days:
            raise BacktestDataError(
                f"股票 {prices[0].symbol} 行情自 {prices[0].trade_date} 起，"
                f"至回测起点 {start_date} 仅 {coverage} 个交易日，"
                f"不足要求的预热期 {strategy.warmup_days} 个交易日"
            )

    def _build_order_book(
        self,
        prices: list[PriceData],
        weights: pd.DataFrame,
        costs: dict[str, float],
        initial_cash: float,
    ) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
        """按"第 i-1 日信号 → 第 i 日开盘成交"生成订单簿与 VectorBT 输入序列。

        调仓数量 = 目标仓位 × 当日总资产 / 执行价，股数取整（int）；
        目标仓位与当前持仓不同才下单。费用精确拆分：
        佣金（每笔不低于最低佣金）+ 过户费双边，印花税仅卖出。
        """
        n = len(prices)
        size = np.zeros(n, dtype=float)
        exec_prices = np.array([p.open for p in prices], dtype=float)
        fee_arr = np.zeros(n, dtype=float)
        trades: list[dict[str, Any]] = []
        cash = initial_cash
        shares = 0
        current_target = 0.0
        slip = float(costs["slippage_rate"])
        for i in range(1, n):
            # 第 i-1 日的信号决定第 i 日开盘的调仓；第一天无前日信号不下单
            prev = weights.iloc[i - 1]
            target = float(prev["target_weight"])
            if target == current_target:
                continue
            open_price = prices[i].open
            if target > current_target:
                direction = "buy"
                exec_price = open_price * (1 + slip)
            else:
                direction = "sell"
                exec_price = open_price * (1 - slip)
            target_shares = int(target * (cash + shares * open_price) / exec_price)
            qty = target_shares - shares
            if qty == 0:
                continue  # 取整后无实际仓位变化
            if direction == "sell":
                qty = -qty
            notional = qty * exec_price
            fees = self._calc_fees(notional, direction, costs)
            if direction == "buy":
                cash -= notional + fees
            else:
                cash += notional - fees
            shares = target_shares
            current_target = target
            trades.append(
                {
                    "signal_date": prices[i - 1].trade_date,
                    "trade_date": prices[i].trade_date,
                    "reason": prev["signal"],
                    "direction": direction,
                    "price": exec_price,
                    "quantity": qty,
                    "fees": fees,
                    "notional": notional,
                }
            )
            size[i] = qty if direction == "buy" else -qty
            exec_prices[i] = exec_price
            fee_arr[i] = fees
        trades_df = pd.DataFrame(
            trades,
            columns=[
                "signal_date",
                "trade_date",
                "reason",
                "direction",
                "price",
                "quantity",
                "fees",
                "notional",
            ],
        )
        return trades_df, size, exec_prices, fee_arr

    @staticmethod
    def _calc_fees(notional: float, direction: str, costs: dict[str, float]) -> float:
        """单笔费用：双边佣金（不低于最低佣金）+ 双边过户费 + 仅卖出印花税。"""
        commission = max(
            notional * float(costs["commission_rate"]),
            float(costs["minimum_commission"]),
        )
        transfer = notional * float(costs["transfer_fee_rate"])
        stamp = notional * float(costs["stamp_duty_rate"]) if direction == "sell" else 0.0
        return commission + transfer + stamp

    def _align_benchmark(
        self, benchmark: BenchmarkSpec, idx: pd.DatetimeIndex, data_start: date, data_end: date
    ) -> pd.Series:
        """基准收盘序列前向对齐到策略净值日，统一从 1.0 起算。"""
        raw = self._provider.fetch_benchmark(benchmark, data_start, data_end)
        bench = pd.Series(raw.to_numpy(), index=pd.DatetimeIndex(raw.index))
        aligned = bench.reindex(idx).ffill()
        na_mask = aligned.isna()
        if na_mask.any():
            missing = aligned.index.to_numpy()[na_mask.to_numpy()]
            raise BacktestDataError(
                f"基准 {benchmark.id} 在 {pd.Timestamp(missing[0]).date()} 至 "
                f"{pd.Timestamp(missing[-1]).date()} 无数据，无法对齐净值日"
            )
        return aligned / aligned.iloc[0]

    @staticmethod
    def _compute_metrics(
        equity: pd.DataFrame,
        trades: pd.DataFrame,
        request: BacktestRequest,
    ) -> tuple[dict[str, float], list[str]]:
        """仅 [start, end] 正式区间进入指标统计（预热期只用于信号计算）。"""
        mask = (equity.index >= pd.Timestamp(request.start_date)) & (
            equity.index <= pd.Timestamp(request.end_date)
        )
        window = equity.loc[mask]
        n_days = len(window)
        if n_days == 0:
            raise BacktestDataError(
                f"正式区间 {request.start_date} 至 {request.end_date} 内无行情数据"
            )
        net = window["策略净值"]
        cumulative = float(net.iloc[-1]) - 1.0
        years = n_days / TRADING_DAYS
        annual = (1.0 + cumulative) ** (1.0 / years) - 1.0 if years > 0 else 0.0
        max_drawdown = float((net / net.cummax() - 1.0).min())
        returns = net.pct_change().dropna()
        if len(returns) >= 2:
            std = float(returns.std())
            annual_vol = std * math.sqrt(TRADING_DAYS)
            sharpe = (
                float(returns.mean() / std * math.sqrt(TRADING_DAYS)) if std > 0 else 0.0
            )
        else:
            annual_vol, sharpe = 0.0, 0.0
        bench_cumulative = float(window["基准净值"].iloc[-1]) - 1.0
        excess = cumulative - bench_cumulative  # 超额收益 = 策略累计 - 基准累计
        in_window = (trades["trade_date"] >= request.start_date) & (
            trades["trade_date"] <= request.end_date
        )
        trade_count = float(int(in_window.sum()))
        warnings: list[str] = []
        if trade_count == 0:
            warnings.append("正式区间内无成交，指标仅反映空仓状态下的净值变化")
        return (
            {
                "累计收益": cumulative,
                "年化收益": annual,
                "最大回撤": max_drawdown,
                "年化波动率": annual_vol,
                "夏普比率": sharpe,
                "交易次数": trade_count,
                "超额收益": excess,
            },
            warnings,
        )

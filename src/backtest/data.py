"""回测历史行情与基准数据 — 日期裁剪与 DataFrame 标准化。"""

import logging
from datetime import date, timedelta

import pandas as pd

from backtest.models import BenchmarkSpec
from data.akshare import AkShareAdapter, _ak_csindex
from data.schemas import PriceData

logger = logging.getLogger(__name__)

# 相邻数据点最大允许间隔（日历日）：覆盖周末（3 天）与中国长假（最长约 9-10 天），
# 超过即视为行情缺口，无法连续计算净值。
MAX_GAP_DAYS = 15

# 区间边界容差（自然日）：start/end 可能落在周末或长假，基准首个/末个交易日
# 略晚/略早于请求日期属正常；超过 7 天说明区间边界数据缺失（如接口从指数基日截断）。
BOUNDARY_TOLERANCE_DAYS = 7


class BacktestDataError(Exception):
    """回测数据异常：日期范围、数据缺失与连续性错误。"""


def validate_price_history(prices: list[PriceData], start: date, end: date) -> None:
    """校验个股日线序列：升序、无重复、无缺口。

    连续净值无法计算时必须终止，故缺口一律抛 BacktestDataError。
    """
    if not prices:
        raise BacktestDataError(f"区间 {start} 至 {end} 内无行情数据，无法计算连续净值")
    dates = [item.trade_date for item in prices]
    for i in range(1, len(dates)):
        prev, cur = dates[i - 1], dates[i]
        if cur < prev:
            raise BacktestDataError(f"行情日期乱序（{prev} → {cur}），无法计算连续净值")
        if cur == prev:
            raise BacktestDataError(f"行情日期重复（{cur}），无法计算连续净值")
        gap = (cur - prev).days
        if gap > MAX_GAP_DAYS:
            raise BacktestDataError(
                f"行情在 {prev} 至 {cur} 间存在缺口（间隔 {gap} 天），无法计算连续净值"
            )


class HistoricalPriceProvider:
    """历史个股行情与基准收盘数据提供者。"""

    def __init__(self, adapter: AkShareAdapter):
        self._adapter = adapter

    def fetch_stock(self, symbol: str, start: date, end: date) -> list[PriceData]:
        """按指定区间获取个股日线行情，原样透传适配器结果。"""
        if start > end:
            raise BacktestDataError(f"日期区间无效: start={start} 晚于 end={end}")
        return self._adapter.fetch(
            symbol,
            data_type="price",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )

    def fetch_benchmark(self, benchmark: BenchmarkSpec, start: date, end: date) -> pd.Series:
        """获取基准收盘点位序列，索引为交易日、值为收盘点位。

        中证接口仅返回日期与收盘两列，这里统一归一化为 date→close 序列；
        基准只用于净值比较，不要求 OHLCV。
        """
        if start > end:
            raise BacktestDataError(f"日期区间无效: start={start} 晚于 end={end}")
        df = _ak_csindex(
            symbol=benchmark.symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or len(df) == 0:
            raise BacktestDataError(f"基准 {benchmark.id} 在 {start} 至 {end} 内无数据")

        # 防御性兼容中文（日期/收盘）与英文（date/close）两种列名
        date_col = "date" if "date" in df.columns else "日期"
        close_col = "close" if "close" in df.columns else "收盘"
        frame = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(df[date_col], errors="coerce"),
                "close": pd.to_numeric(df[close_col], errors="coerce"),
            }
        )
        # 非关键缺失产生 warning：非法日期/收盘值在丢弃前记录行数
        dropped = int(frame.isna().any(axis=1).sum())
        if dropped:
            logger.warning(
                "基准 %s 原始数据含 %d 行无法解析的日期或收盘值，已丢弃",
                benchmark.id,
                dropped,
            )
        frame = frame.dropna()
        # 提取纯 Python 值：区间裁剪、排序去重与连续性校验在列表上进行，
        # 规避 pandas 类型推断歧义
        dates: list[date] = [ts.date() for ts in frame["trade_date"]]
        closes = frame["close"].to_numpy()
        rows = [(d, float(c)) for d, c in zip(dates, closes, strict=True)]
        rows = [(d, c) for d, c in rows if start <= d <= end]
        rows = sorted(dict(rows).items())  # 按键排序并按日期去重（保留最后值）
        if not rows:
            raise BacktestDataError(f"基准 {benchmark.id} 在 {start} 至 {end} 内无数据")

        # 区间边界校验：基准序列必须覆盖请求区间 [start, end] 两端（含容差）。
        # 边界判定基准是请求区间本身而非股票数据；H11001/H11025 基日均早于
        # 一般回测起点，正常请求不会触发。首/末日超出容差说明区间边界数据缺失。
        first_day, last_day = rows[0][0], rows[-1][0]
        if first_day - start > timedelta(days=BOUNDARY_TOLERANCE_DAYS):
            raise BacktestDataError(
                f"基准 {benchmark.id} 缺少 {start} 至 {first_day} 前的行情"
                f"（序列始于 {first_day}，晚于回测起点 {BOUNDARY_TOLERANCE_DAYS} 天）"
            )
        if end - last_day > timedelta(days=BOUNDARY_TOLERANCE_DAYS):
            raise BacktestDataError(
                f"基准 {benchmark.id} 缺少 {last_day} 后至 {end} 的行情"
                f"（序列止于 {last_day}，早于回测终点 {BOUNDARY_TOLERANCE_DAYS} 天）"
            )

        # 基准交易日缺口：股票有交易日而基准在该日缺失时无法对齐净值日
        trade_dates = [d for d, _ in rows]
        for i in range(1, len(trade_dates)):
            prev, cur = trade_dates[i - 1], trade_dates[i]
            gap = (cur - prev).days
            if gap > MAX_GAP_DAYS:
                raise BacktestDataError(
                    f"基准 {benchmark.id} 在 {prev} 至 {cur} 间存在缺口"
                    f"（间隔 {gap} 天），无法对齐股票净值日计算连续净值"
                )

        return pd.Series(
            [close for _, close in rows],
            index=pd.Index(trade_dates, name="trade_date"),
            name=benchmark.name,
        )

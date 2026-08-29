"""回测历史行情与基准数据 — 日期裁剪与 DataFrame 标准化。"""

from datetime import date

import pandas as pd

from backtest.models import BenchmarkSpec
from data.akshare import AkShareAdapter, _ak_csindex
from data.schemas import PriceData

# 相邻数据点最大允许间隔（日历日）：覆盖周末（3 天）与中国长假（最长约 9-10 天），
# 超过即视为行情缺口，无法连续计算净值。
MAX_GAP_DAYS = 15


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
        ).dropna()
        # 提取纯 Python 值：区间裁剪、排序去重与连续性校验在列表上进行，
        # 规避 pandas 类型推断歧义
        dates: list[date] = [ts.date() for ts in frame["trade_date"]]
        closes = frame["close"].to_numpy()
        rows = [(d, float(c)) for d, c in zip(dates, closes, strict=True)]
        rows = [(d, c) for d, c in rows if start <= d <= end]
        rows = sorted(dict(rows).items())  # 按键排序并按日期去重（保留最后值）
        if not rows:
            raise BacktestDataError(f"基准 {benchmark.id} 在 {start} 至 {end} 内无数据")

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

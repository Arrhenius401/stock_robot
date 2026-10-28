"""回测历史行情与基准数据 — 日期裁剪与 DataFrame 标准化。"""

import logging
from datetime import date, datetime, timedelta

import pandas as pd

from backtest.models import BenchmarkSpec
from data.akshare import AkShareAdapter, fetch_index_history_result
from data.collection_state import ValueResult, make_record
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
        """兼容包装：返回行情列表。"""
        return self.fetch_stock_result(symbol, start, end).value or []

    def fetch_stock_result(self, symbol: str, start: date, end: date) -> ValueResult[list[PriceData]]:
        """按指定区间获取个股日线并返回采集记录。"""
        if start > end:
            raise BacktestDataError(f"日期区间无效: start={start} 晚于 end={end}")
        result = self._adapter.fetch_result(
            symbol,
            data_type="price",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        rows = result.rows or []
        if rows:
            record = make_record(
                result.record.source if result.record else "AkShare",
                "complete",
                collected_at=datetime.now().astimezone(),
            )
        elif result.record is not None:
            record = make_record(
                result.record.source or "AkShare",
                "failed",
                diagnostic=result.record,
                collected_at=datetime.now().astimezone(),
            )
        else:
            record = make_record("AkShare", "empty", collected_at=datetime.now().astimezone())
        return ValueResult(rows, record, list(result.errors))

    def fetch_benchmark(self, benchmark: BenchmarkSpec, start: date, end: date) -> pd.Series:
        """兼容包装：返回基准收盘序列。"""
        result = self.fetch_benchmark_result(benchmark, start, end)
        assert result.value is not None
        return result.value

    def fetch_benchmark_result(self, benchmark: BenchmarkSpec, start: date, end: date) -> ValueResult[pd.Series]:
        """获取基准收盘点位序列并返回采集记录。"""
        if start > end:
            raise BacktestDataError(f"日期区间无效: start={start} 晚于 end={end}")
        result = fetch_index_history_result(
            benchmark.symbol,
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
        )
        if result.record.result_state == "failed":
            raise BacktestDataError(
                f"基准 {benchmark.id} 行情请求失败：{result.record.error_message}"
            )
        if result.value is None:
            raise BacktestDataError(f"基准 {benchmark.id} 在 {start} 至 {end} 内无数据")
        df = result.value
        # 防御性兼容中文（日期/收盘）与英文（date/close）两种列名
        date_col = "date" if "date" in df.columns else "日期"
        close_col = "close" if "close" in df.columns else "收盘"
        frame = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(df[date_col], errors="coerce"),
                "close": pd.to_numeric(df[close_col], errors="coerce"),
            }
        )
        dropped = int(frame.isna().any(axis=1).sum())
        if dropped:
            logger.warning(
                "基准 %s 原始数据含 %d 行无法解析的日期或收盘值，已丢弃",
                benchmark.id,
                dropped,
            )
        frame = frame.dropna()
        dates: list[date] = [ts.date() for ts in frame["trade_date"]]
        closes = frame["close"].to_numpy()
        rows = [(d, float(c)) for d, c in zip(dates, closes, strict=True)]
        rows = [(d, c) for d, c in rows if start <= d <= end]
        rows = sorted(dict(rows).items())
        if not rows:
            raise BacktestDataError(f"基准 {benchmark.id} 在 {start} 至 {end} 内无数据")
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
        trade_dates = [d for d, _ in rows]
        for i in range(1, len(trade_dates)):
            prev, cur = trade_dates[i - 1], trade_dates[i]
            gap = (cur - prev).days
            if gap > MAX_GAP_DAYS:
                raise BacktestDataError(
                    f"基准 {benchmark.id} 在 {prev} 至 {cur} 间存在缺口"
                    f"（间隔 {gap} 天），无法对齐股票净值日计算连续净值"
                )
        series = pd.Series(
            [close for _, close in rows],
            index=pd.Index(trade_dates, name="trade_date"),
            name=benchmark.name,
        )
        return ValueResult(series, result.record)

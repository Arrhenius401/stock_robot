"""配置雷达的数据提供者与上游限流边界。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from time import monotonic, sleep
from typing import Any, ClassVar, Protocol, cast

import pandas as pd


class RadarDataError(Exception):
    """雷达数据源不可用或返回的数据不完整。"""


class ProviderCircuitOpenError(RadarDataError):
    """同一数据源本轮已熔断。"""


class RadarDataProvider(Protocol):
    """不同资产类型的数据提供者契约。"""

    def supports(self, asset_type: str) -> bool:
        """返回是否支持指定资产类型。"""
        raise NotImplementedError

    def fetch_spot(self) -> pd.DataFrame:
        """获取并标准化 ETF 现货行情。"""
        raise NotImplementedError

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """获取并标准化日线行情。"""
        raise NotImplementedError


class RequestPacer:
    """串行请求节流器，支持注入时钟以便离线测试。"""

    def __init__(
        self,
        minimum_interval_seconds: float = 1.0,
        *,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ):
        if minimum_interval_seconds <= 0:
            raise ValueError("minimum_interval_seconds 必须大于 0")
        self.minimum_interval_seconds = minimum_interval_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._last_request_at: float | None = None

    def wait_turn(self) -> None:
        """等待至满足两次请求的最小间隔。"""
        now = self._clock()
        if self._last_request_at is not None:
            remaining = self.minimum_interval_seconds - (now - self._last_request_at)
            if remaining > 0:
                self._sleeper(remaining)
        self._last_request_at = self._clock()


class AkShareETFDataProvider:
    """AkShare ETF 数据适配器；只支持 ETF，且每次调用受节流保护。"""

    _SPOT_COLUMNS: ClassVar[dict[str, str]] = {
        "代码": "symbol", "名称": "name", "最新价": "close", "成交额": "amount"
    }
    _DAILY_COLUMNS: ClassVar[dict[str, str]] = {
        "日期": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
    }

    def __init__(
        self,
        *,
        pacer: RequestPacer | None = None,
        spot_fetcher: Callable[[], pd.DataFrame] | None = None,
        daily_fetcher: Callable[..., pd.DataFrame] | None = None,
    ):
        self._pacer = pacer or RequestPacer()
        self._spot_fetcher = spot_fetcher or self._akshare_spot
        self._daily_fetcher = daily_fetcher or self._akshare_daily
        self._circuit_open = False

    def supports(self, asset_type: str) -> bool:
        """首期仅支持 ETF。"""
        return asset_type == "etf"

    def fetch_spot(self) -> pd.DataFrame:
        """获取 ETF 现货并统一为内部字段。"""
        return self._fetch_and_normalize(self._spot_fetcher, self._SPOT_COLUMNS, ["symbol", "name"])

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """获取 ETF 前复权日线并统一为内部字段。"""
        if start > end:
            raise ValueError("start 不能晚于 end")
        return self._fetch_and_normalize(
            lambda: self._daily_fetcher(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
            ),
            self._DAILY_COLUMNS,
            ["date", "open", "high", "low", "close", "amount"],
        )

    def _fetch_and_normalize(
        self,
        fetcher: Callable[[], pd.DataFrame],
        columns: dict[str, str],
        required: list[str],
    ) -> pd.DataFrame:
        if self._circuit_open:
            raise ProviderCircuitOpenError("AkShare ETF 数据源本轮已熔断")
        self._pacer.wait_turn()
        try:
            frame = fetcher()
        except Exception as exc:  # 第三方 SDK/network 边界，统一熔断本轮请求
            self._circuit_open = True
            raise RadarDataError("AkShare ETF 请求失败，已熔断本轮请求") from exc
        if not isinstance(frame, pd.DataFrame):
            self._circuit_open = True
            raise RadarDataError("AkShare ETF 返回类型不是 DataFrame，已熔断本轮请求")
        normalized = frame.rename(columns=columns).copy()
        missing = set(required) - set(normalized.columns)
        if missing:
            self._circuit_open = True
            raise RadarDataError(f"AkShare ETF 缺少必需字段: {', '.join(sorted(missing))}")
        if normalized.empty:
            return normalized.reindex(columns=list(dict.fromkeys(columns.values())))
        if "symbol" in normalized:
            normalized["symbol"] = normalized["symbol"].astype(str).str.zfill(6)
        if "date" in normalized:
            normalized["date"] = pd.to_datetime(normalized["date"], errors="raise").dt.date
        for column in {"open", "high", "low", "close", "volume", "amount"} & set(normalized.columns):
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        return cast(pd.DataFrame, normalized[list(dict.fromkeys(columns.values()))])

    @staticmethod
    def _akshare_spot() -> pd.DataFrame:
        """延迟导入 AkShare，避免导入 CLI 时发起第三方初始化。"""
        import akshare as ak

        frame: Any = ak.fund_etf_spot_em()
        return frame

    @staticmethod
    def _akshare_daily(**kwargs: str) -> pd.DataFrame:
        """调用 AkShare ETF 日线接口。"""
        import akshare as ak

        frame: Any = ak.fund_etf_hist_em(**kwargs)
        return frame


class SinaETFDataProvider:
    """新浪 ETF 日线适配器，用于 Eastmoney 不可用时的免费备源。"""

    def __init__(self, *, fetcher: Callable[[str], pd.DataFrame] | None = None):
        self._fetcher = fetcher or self._akshare_daily

    def supports(self, asset_type: str) -> bool:
        """首期仅支持 ETF。"""
        return asset_type == "etf"

    def fetch_spot(self) -> pd.DataFrame:
        """新浪历史接口没有可靠的全市场现货契约。"""
        raise RadarDataError("新浪 ETF 历史接口不提供统一现货快照")

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """取得全量历史后在本地截取区间，成交额以收盘价乘成交量估算。"""
        exchange_symbol = _sina_symbol(symbol)
        try:
            frame = self._fetcher(exchange_symbol)
        except Exception as exc:  # 第三方 SDK/network 边界需转换为统一异常
            raise RadarDataError("新浪 ETF 日线请求失败") from exc
        required = {"date", "open", "high", "low", "close", "volume"}
        missing = required - set(frame.columns)
        if missing:
            raise RadarDataError(f"新浪 ETF 日线缺少字段: {', '.join(sorted(missing))}")
        normalized = frame.copy()
        normalized["date"] = pd.to_datetime(normalized["date"], errors="raise").dt.date
        for column in ("open", "high", "low", "close", "volume"):
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        normalized["amount"] = normalized["close"] * normalized["volume"]
        result = normalized[(normalized["date"] >= start) & (normalized["date"] <= end)]
        if result.empty:
            raise RadarDataError("新浪 ETF 日线在请求区间没有数据")
        return cast(pd.DataFrame, result[["date", "open", "high", "low", "close", "volume", "amount"]])

    @staticmethod
    def _akshare_daily(symbol: str) -> pd.DataFrame:
        """延迟导入 AkShare；新浪接口需交易所前缀。"""
        import akshare as ak

        frame: Any = ak.fund_etf_hist_sina(symbol=symbol)
        return frame


class OfficialExchangeETFDataProvider:
    """交易所公开日终文件适配器的接入点。

    交易所公开页面的下载格式与地址会调整，首期不把未验证 URL 硬编码进运行时；
    调用方可注入已下载且审核过的日终文件读取器，作为第三层兜底。
    """

    def __init__(self, *, daily_fetcher: Callable[[str, date, date], pd.DataFrame] | None = None):
        self._daily_fetcher = daily_fetcher

    def supports(self, asset_type: str) -> bool:
        return asset_type == "etf"

    def fetch_spot(self) -> pd.DataFrame:
        raise RadarDataError("交易所日终文件不提供盘中现货")

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        if self._daily_fetcher is None:
            raise RadarDataError("交易所公开日终文件尚未配置")
        frame = self._daily_fetcher(symbol, start, end)
        required = {"date", "open", "high", "low", "close", "amount"}
        missing = required - set(frame.columns)
        if missing:
            raise RadarDataError(f"交易所日终文件缺少字段: {', '.join(sorted(missing))}")
        return frame


class FallbackETFDataProvider:
    """串行主备源：一个源失败只切换当前标的，不中断整个刷新。"""

    def __init__(self, providers: Sequence[RadarDataProvider]):
        if not providers:
            raise ValueError("至少需要一个 ETF 数据提供者")
        self._providers = tuple(providers)

    def supports(self, asset_type: str) -> bool:
        return all(provider.supports(asset_type) for provider in self._providers)

    def fetch_spot(self) -> pd.DataFrame:
        errors: list[str] = []
        for provider in self._providers:
            try:
                return provider.fetch_spot()
            except RadarDataError as exc:
                errors.append(str(exc))
        raise RadarDataError("；".join(errors))

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        errors: list[str] = []
        for provider in self._providers:
            try:
                return provider.fetch_daily(symbol, start, end)
            except RadarDataError as exc:
                errors.append(f"{type(provider).__name__}: {exc}")
        raise RadarDataError("；".join(errors))


def _sina_symbol(symbol: str) -> str:
    """根据现行池代码推断新浪所需的沪深交易所前缀。"""
    return f"sz{symbol}" if symbol.startswith(("15", "16")) else f"sh{symbol}"

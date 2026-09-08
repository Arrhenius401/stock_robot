"""配置雷达的数据提供者与上游限流边界。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from io import BytesIO
from pathlib import Path
from time import monotonic, sleep
from typing import Any, ClassVar, Protocol, cast
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import pandas as pd
import requests
import yaml


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


class TencentETFDataProvider:
    """腾讯免费复权日线适配器，用于 AkShare 不可用时的独立备源。"""

    def __init__(self, *, fetcher: Callable[[str, date, date], pd.DataFrame] | None = None):
        self._fetcher = fetcher or self._tencent_daily

    def supports(self, asset_type: str) -> bool:
        """首期仅支持 ETF。"""
        return asset_type == "etf"

    def fetch_spot(self) -> pd.DataFrame:
        """该适配器只提供历史日线。"""
        raise RadarDataError("腾讯 ETF 历史接口不提供统一现货快照")

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """取得前复权日线，并以收盘价乘成交量估算成交额。"""
        if start > end:
            raise ValueError("start 不能晚于 end")
        try:
            frame = self._fetcher(symbol, start, end)
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise RadarDataError("腾讯 ETF 日线请求失败") from exc
        required = {"date", "open", "high", "low", "close", "volume"}
        missing = required - set(frame.columns)
        if missing:
            raise RadarDataError(f"腾讯 ETF 日线缺少字段: {', '.join(sorted(missing))}")
        normalized = frame.copy()
        normalized["date"] = pd.to_datetime(normalized["date"], errors="raise").dt.date
        for column in ("open", "high", "low", "close", "volume"):
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        normalized["amount"] = normalized["close"] * normalized["volume"]
        result = normalized[(normalized["date"] >= start) & (normalized["date"] <= end)]
        if result.empty:
            raise RadarDataError("腾讯 ETF 日线在请求区间没有数据")
        return cast(pd.DataFrame, result[["date", "open", "high", "low", "close", "volume", "amount"]])

    @staticmethod
    def _tencent_daily(symbol: str, start: date, end: date) -> pd.DataFrame:
        """调用腾讯公开复权 K 线接口。"""
        market_symbol = _tencent_symbol(symbol)
        response = requests.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={
                "param": f"{market_symbol},day,{start.isoformat()},{end.isoformat()},1000,qfq",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload["data"][market_symbol]["qfqday"]
        return pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "volume"])


class OfficialExchangeETFDataProvider:
    """按受审计配置下载并解析上交所、深交所 ETF 日终 CSV 文件。"""

    _DAILY_COLUMNS: ClassVar[dict[str, str]] = {
        "日期": "date", "交易日期": "date", "date": "date", "Date": "date",
        "开盘": "open", "开盘价": "open", "open": "open", "Open": "open",
        "最高": "high", "最高价": "high", "high": "high", "High": "high",
        "最低": "low", "最低价": "low", "low": "low", "Low": "low",
        "收盘": "close", "收盘价": "close", "close": "close", "Close": "close",
        "成交量": "volume", "volume": "volume", "Volume": "volume",
        "成交额": "amount", "成交金额": "amount", "amount": "amount", "Amount": "amount",
    }

    def __init__(
        self,
        *,
        config_path: Path | None = None,
        daily_fetcher: Callable[[str, date, date], pd.DataFrame] | None = None,
        downloader: Callable[[str], bytes] | None = None,
    ):
        self._config_path = config_path or Path(__file__).parents[2] / "config" / "radar_exchange_sources.yaml"
        self._daily_fetcher = daily_fetcher
        self._downloader = downloader or self._download

    def supports(self, asset_type: str) -> bool:
        return asset_type == "etf"

    def fetch_spot(self) -> pd.DataFrame:
        raise RadarDataError("交易所日终文件不提供盘中现货")

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        if start > end:
            raise ValueError("start 不能晚于 end")
        frame = self._daily_fetcher(symbol, start, end) if self._daily_fetcher else self._fetch_official(symbol, start, end)
        required = {"date", "open", "high", "low", "close", "amount"}
        frame = frame.rename(columns=self._DAILY_COLUMNS).copy()
        missing = required - set(frame.columns)
        if missing:
            raise RadarDataError(f"交易所日终文件缺少字段: {', '.join(sorted(missing))}")
        if "volume" not in frame:
            frame["volume"] = pd.NA
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
        for column in required - {"date"}:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        result = frame[(frame["date"] >= start) & (frame["date"] <= end)]
        if result.empty:
            raise RadarDataError("交易所日终文件在请求区间没有数据")
        return cast(pd.DataFrame, result[["date", "open", "high", "low", "close", "volume", "amount"]])

    def _fetch_official(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """从版本化配置取得对应市场的官方 CSV 下载模板。"""
        source = "szse" if symbol.startswith(("15", "16")) else "sse"
        try:
            payload = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
            settings = payload["sources"][source]
        except (OSError, TypeError, KeyError, yaml.YAMLError) as exc:
            raise RadarDataError("交易所日终数据源配置不可用") from exc
        template = settings.get("url_template") if isinstance(settings, dict) else None
        if not settings.get("enabled") or not isinstance(template, str) or not template:
            raise RadarDataError(f"{source.upper()} 官方日终下载尚未启用")
        url = template.format(symbol=symbol, start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"))
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(("sse.com.cn", "szse.cn")):
            raise RadarDataError("交易所日终下载地址必须为上交所或深交所 HTTPS 域名")
        try:
            content = self._downloader(url)
            return pd.read_csv(BytesIO(content), encoding=str(settings.get("encoding", "utf-8")))
        except (OSError, UnicodeDecodeError, URLError, ValueError, pd.errors.ParserError) as exc:
            raise RadarDataError(f"{source.upper()} 官方日终文件下载或解析失败") from exc

    @staticmethod
    def _download(url: str) -> bytes:
        """下载公开 CSV，设置标识以兼容交易所的基础反爬规则。"""
        request = Request(url, headers={"User-Agent": "Stock-Robot/0.1 (research)"})
        with urlopen(request, timeout=20) as response:
            return response.read()


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


def _tencent_symbol(symbol: str) -> str:
    """根据 ETF 代码推断腾讯行情接口的交易所前缀。"""
    return _sina_symbol(symbol)

"""AkShare 数据源适配器 — A 股数据采集"""
import csv
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta, timezone
from itertools import pairwise
from threading import local
from typing import Any, cast

import akshare as ak
from pandas import NaT

# akshare 无完整类型标注，接口返回值形态随版本变化，统一按 Any 处理
ak = cast(Any, ak)

from data.base import CollectionResult, DataSource
from data.collection_state import ValueResult, make_record
from data.hk_alternatives import baidu_metric, tencent_history
from data.hk_eastmoney import (
    daily_history,
    financial_report_directory,
    financial_statement,
    finite_number,
    stock_snapshot,
    valuation_comparison_snapshot,
)
from data.hk_financial_metrics import GROSS_MARGIN_BASIS, gross_margins
from data.schemas import (
    CapitalFlowData,
    DataCollectionRecord,
    FinancialData,
    IndexPriceData,
    IndexValuationData,
    IndustryData,
    MacroContext,
    NewsData,
    PriceData,
    ValuationData,
)
from data.source_selection import select_history
from utils.numbers import parse_cn_number
from utils.retry import retry_on_network_error

logger = logging.getLogger(__name__)
_THS_GROSS_MARGIN_BASIS = "同花顺财务摘要-销售毛利率（摘要口径，未重算）"


def _hk_history_window(fetch, code: str, metric: str) -> tuple[dict[date, float], int, str]:
    """百度长窗优先五年，失败或空值回退三年，均失败保留短窗。"""
    from utils.numbers import is_positive_finite

    for label, days in (("近五年", 1827), ("近三年", 1096)):
        try:
            points = fetch(code, metric, chart_select=label)
            if points and any(is_positive_finite(value) for value in points.values()):
                return points, days, label
        except Exception as error:
            logger.warning("百度港股%s %s不可用: %s", label, metric, error)
    return {}, 365, "近一年"


def _resolve_eastmoney_to_sw(industry: str) -> tuple[str, str] | None:
    """仅解析本地人工核验的高置信东财→申万唯一映射。"""
    from utils.paths import resource_root

    path = resource_root() / "data" / "eastmoney_sw_mapping.csv"
    try:
        with open(path, encoding="utf-8") as file:
            for row in csv.DictReader(file):
                if row.get("eastmoney_industry") == industry and row.get("confidence") == "high":
                    return row.get("sw_level1", ""), row.get("sw_level2", "")
    except OSError:
        logger.warning("无法读取东财申万映射表")
    return None

# 海外指数代码 → 全球指数接口所需的中文名称
_OVERSEAS_NAME_MAP = {
    "HSI": "恒生指数",
    "HSTECH": "恒生科技指数",
    "HSCEI": "恒生中国企业指数",
    "SPX": "标普500",
    "NDX": "纳斯达克100",
    "IXIC": "纳斯达克综合",
    "DJI": "道琼斯工业平均",
}

# 海外指数新浪源代码：港股直传代码，美股带 "." 前缀
_OVERSEAS_SINA_SYMBOLS = {
    "HSI": "HSI",
    "HSTECH": "HSTECH",
    "HSCEI": "HSCEI",
    "SPX": ".INX",
    "NDX": ".NDX",
    "IXIC": ".IXIC",
    "DJI": ".DJI",
}


def _row_get_any(row: Any, *keys: str, default: Any = None) -> Any:
    """按候选列名读取一行数据，兼容不同 AkShare 接口的中英文列名。"""
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return default


def get_total_shares_result(symbol: str, financials: list | None = None) -> ValueResult[float]:
    """总股本三级链：东财轻量接口 → 新浪流通股本 → 财报反推（离线兜底）

    财报反推口径：累计净利润 ÷ 累计基本每股收益，禁止单季/累计混用。
    """
    # 1. 东财轻量接口（在线优先）
    try:
        df: Any = _ak_individual_info_em(symbol)
        if df is not None and "item" in df.columns and "value" in df.columns:
            info = dict(zip(df["item"], df["value"]))
            shares = parse_cn_number(str(info.get("总股本", "")))
            if shares is not None and shares > 0:
                return ValueResult(shares, make_record("东财总股本", "complete", collected_at=datetime.now().astimezone()))
    except Exception:
        logger.debug("东财总股本获取失败，切换新浪源")
    # 2. 新浪流通股本（在线）
    try:
        end = datetime.now().astimezone().date().strftime("%Y%m%d")
        start = (datetime.now().astimezone().date() - timedelta(days=10)).strftime("%Y%m%d")
        df: Any = _ak_daily(symbol=symbol, start_date=start, end_date=end, adjust="")
        if df is not None and "outstanding_share" in df.columns and len(df) > 0:
            last = df["outstanding_share"].iloc[-1]
            if last is not None and str(last) not in ("nan", "None") and float(last) > 0:
                return ValueResult(float(last), make_record("新浪流通股本", "complete", collected_at=datetime.now().astimezone()))
    except Exception:
        logger.debug("新浪流通股本获取失败，切换财报反推")
    # 3. 财报反推（离线兜底）
    if financials:
        fin = sorted(financials, key=lambda x: x.fiscal_quarter)
        latest = fin[-1]
        # >0 判断统一口径，避免 NaN 真值通过
        if latest.net_profit is not None and latest.basic_eps is not None and latest.net_profit > 0 and latest.basic_eps > 0:
            return ValueResult(latest.net_profit / latest.basic_eps, make_record(
                "财报反推", "complete", collected_at=datetime.now().astimezone()))
    return ValueResult(None, make_record("总股本", "failed", error_kind="empty",
                                         error_message="总股本来源均不可用"))


def get_total_shares(symbol: str, financials: list | None = None) -> float | None:
    """兼容旧调用方，仅返回股本数值。"""
    return get_total_shares_result(symbol, financials).value


def _get_industry_name(symbol: str) -> str:
    """行业名两级链：东财轻量接口 → 本地映射表（离线兜底）"""
    try:
        df: Any = _ak_individual_info_em(symbol)
        if df is not None and "item" in df.columns and "value" in df.columns:
            info = dict(zip(df["item"], df["value"]))
            ind = str(info.get("行业", "") or "").strip()
            if ind and ind != "未知":
                return ind
    except Exception:
        logger.debug("东财行业信息获取失败，切换本地映射表")
    try:
        from data.industry_classifier import IndustryClassifier
        cls = IndustryClassifier().lookup(symbol)
        if cls.sw_level1 and cls.sw_level1 not in ("综合", "未知"):
            return cls.sw_level1
    except Exception:
        logger.debug("本地行业映射表不可用")
    return ""


def _normalize_percentage(value: float | None) -> float | None:
    """同花顺百分比字段始终按百分数返回，转小数时不按数值大小猜单位。"""
    return value / 100.0 if value is not None else None


@retry_on_network_error()
def _ak_hist(**kwargs):
    return ak.stock_zh_a_hist(**kwargs)


@retry_on_network_error()
def _ak_daily(symbol, start_date, end_date, adjust):
    """新浪源日线数据，symbol 格式: sz000001 / sh600000。"""
    if symbol.startswith("6"):
        sina_symbol = f"sh{symbol}"
    else:
        sina_symbol = f"sz{symbol}"
    return ak.stock_zh_a_daily(symbol=sina_symbol, start_date=start_date, end_date=end_date, adjust=adjust)


@retry_on_network_error()
def _ak_csindex(symbol, start_date, end_date):
    """中证指数公司日线接口（H11025/H11001/000300 等基准指数，返回日期与收盘）"""
    return ak.stock_zh_index_hist_csindex(symbol=symbol, start_date=start_date, end_date=end_date)


def _index_error_kind(error: Exception) -> str:
    if isinstance(error, TimeoutError) or "Timeout" in type(error).__name__ or "超时" in str(error):
        return "timeout"
    return "fetch_failed"


def fetch_index_history_result(symbol: str, start_date: str, end_date: str) -> ValueResult[Any]:
    """中证指数日线的统一结果入口；失败只暴露安全诊断。"""
    try:
        frame = _ak_csindex(symbol=symbol, start_date=start_date, end_date=end_date)
    except Exception as error:  # 第三方网络与响应边界
        kind = _index_error_kind(error)
        logger.warning("中证指数行情请求失败，类型：%s", kind)
        message = "中证指数行情请求超时" if kind == "timeout" else "中证指数行情请求失败"
        return ValueResult(None, make_record("中证指数官网", "failed", error_kind=kind, error_message=message))
    if frame is None or len(frame) == 0:
        return ValueResult(None, make_record("中证指数官网", "empty", error_message="中证指数在请求区间没有数据"))
    return ValueResult(frame, make_record("中证指数官网", "complete", collected_at=datetime.now().astimezone()))

@retry_on_network_error()
def _fetch_overseas_index_sina(symbol: str):
    """海外指数新浪源回退：东财全球指数接口失效时的替代日线源。

    港股走 stock_hk_index_daily_sina（代码直传），美股走 index_us_stock_sina
    （带 "." 前缀）。无映射的符号返回 None，由调用方降级为空数据。
    """
    sina_symbol = _OVERSEAS_SINA_SYMBOLS.get(symbol)
    if sina_symbol is None:
        return None
    if sina_symbol.startswith("."):
        return ak.index_us_stock_sina(symbol=sina_symbol)
    return ak.stock_hk_index_daily_sina(symbol=sina_symbol)


@retry_on_network_error()
def _ak_individual_info_em(symbol):
    """单只股票基本信息接口（轻量，含行业字段）"""
    return ak.stock_individual_info_em(symbol=symbol)


@retry_on_network_error()
def _ak_news(symbol):
    return ak.stock_news_em(symbol=symbol)


@retry_on_network_error()
def _ak_board_industry_cons_em(symbol):
    """行业板块成分股接口（带重试）"""
    return ak.stock_board_industry_cons_em(symbol=symbol)


def _parse_date(value) -> date:
    """解析 AkShare 日期值为 date（支持 date/datetime/字符串，格式 YYYY-MM-DD / YYYYMMDD）"""
    if value is NaT:
        raise ValueError("日期为空 NaT")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).astimezone().date()
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: {value}")


def _fetch_sw_peers(sw_level2: str, sw_level1: str) -> tuple[list[dict[str, Any]], str]:
    """按申万二级优先、一级回退获取完整同行池。

    行业树的一级、二级节点都能直接返回完整成分表。一次分析最多请求二级和
    一级各一次，避免旧实现按模糊名称遍历多个三级行业导致的慢、错配和限流。
    """
    from data.industry_mapping_builder import fetch_constituents, fetch_taxonomy_codes

    try:
        level1_codes, level2_codes = fetch_taxonomy_codes()
    except Exception as e:
        logger.warning("无法获取申万行业代码: %s", e)
        return [], ""

    candidates = [
        (level2_codes.get(sw_level2), "申万二级"),
        (level1_codes.get(sw_level1), "申万一级"),
    ]
    for industry_code, scope in candidates:
        if not industry_code:
            continue
        try:
            stocks = fetch_constituents(industry_code)
        except Exception as e:
            logger.warning("获取%s成分股失败 %s: %s", scope, industry_code, e)
            continue
        peers = [
            {
                "symbol": stock["symbol"],
                "name": stock["name"],
                "market_cap": stock["market_cap"] * 1e8,
                "pe_ttm": stock["pe_ttm"],
                "pb": stock["pb"],
            }
            for stock in stocks
            if stock["market_cap"] is not None and stock["market_cap"] > 0
        ]
        if peers:
            return peers, scope
    return [], ""


def _parse_debt_new(bs_df: Any) -> dict[str, tuple[float | None, float | None, float | None]]:
    """解析 THS 新长表资产负债表 → {报告期: (equity, assets, common_equity)}

    common_equity 为普通股东权益（所有者权益 − 其他权益工具 − 优先股），
    PB 口径与市场惯例（腾讯/东财）一致；相关指标缺失时等于 equity。
    """
    balance_map: dict[str, tuple[float | None, float | None, float | None]] = {}
    per_date: dict[str, dict[str, float | None]] = {}
    for _, row in bs_df.iterrows():
        period = str(row.get("report_date", ""))
        try:
            period_date = datetime.strptime(period, "%Y-%m-%d").astimezone().date().isoformat()
        except ValueError:
            continue
        name = str(row.get("metric_name", ""))
        if name in ("assets_total", "holder_equity_total", "debt_and_equity_total",
                    "other_equity_tools", "preferred_stock"):
            per_date.setdefault(period_date, {})[name] = parse_cn_number(row.get("value"))
    for period_date, vals in per_date.items():
        assets = vals.get("assets_total")
        if assets is None:
            assets = vals.get("debt_and_equity_total")
        equity = vals.get("holder_equity_total")
        common = equity
        if equity is not None:
            common = equity - (vals.get("other_equity_tools") or 0.0) - (vals.get("preferred_stock") or 0.0)
        balance_map[period_date] = (equity, assets, common)
    return balance_map


class AkShareAdapter(DataSource):
    """AkShare 数据源适配器 — 支持 A 股全部数据类型"""

    def __init__(self):
        # 同一适配器供多个采集线程复用；失败诊断不能共享实例字段。
        self._collection_diagnostics = local()

    def get_collection_error(self) -> DataCollectionRecord | None:
        """返回当前线程最后一次采集的安全诊断。"""
        return getattr(self._collection_diagnostics, "record", None)

    def _record_collection_error(self, source: str, error: Exception):
        previous = self.get_collection_error()
        kind = "timeout" if isinstance(error, TimeoutError) or "Timeout" in type(error).__name__ else "fetch_failed"
        self._collection_diagnostics.record = DataCollectionRecord(
            sources=previous.sources if previous is not None else [],
            source=source, error_kind=kind,
            error_message="数据请求超时" if kind == "timeout" else "数据请求失败",
        )

    def _append_collection_error(self, source: str, error: Exception):
        """来源切换保留所有安全诊断；成功来源仍由返回模型提供。"""
        previous = self.get_collection_error()
        self._record_collection_error(source, error)
        current = self.get_collection_error()
        if current is not None:
            message = f"{source}：{current.error_message}"
            if previous is not None:
                message = f"{previous.error_message}；{message}"
            self._collection_diagnostics.record = current.model_copy(update={"error_message": message})

    def supports(self, market: str, data_type: str) -> bool:
        if market == "a-shares" and data_type in (
            "price", "financial", "valuation", "industry", "news"
        ):
            return True
        if market == "hk" and data_type in ("price", "financial", "valuation", "news"):
            return True
        if market == "us" and data_type in ("price", "financial", "valuation", "industry", "news"):
            return True
        elif data_type in ("index_price", "index_valuation", "index_capital_flow",
                           "index_macro", "index_sentiment"):
            return market in ("a-shares", "hk", "us")
        return False

    def fetch(self, symbol: str, **kwargs) -> list:
        """遗留路径兼容包装；新路径使用 fetch_result。"""
        return self._fetch_impl(symbol, **kwargs)[0]

    def fetch_result(self, symbol: str, **kwargs) -> CollectionResult:
        """统一取数入口：数据与诊断在同一次调用中返回。"""
        rows, record = self._fetch_impl(symbol, **kwargs)
        return CollectionResult(rows=rows, record=record)

    def _fetch_impl(self, symbol: str, **kwargs) -> tuple[list, DataCollectionRecord | None]:
        data_type = kwargs.get("data_type", "price")
        self._collection_diagnostics.record = None
        labels = {
            "financial": "同花顺财报", "valuation": "腾讯估值快照",
            "industry": "AkShare 行业与同行池", "news": "东方财富新闻与公告",
        }
        if kwargs.get("market") == "hk":
            labels.update(
                price="东方财富港股行情（不复权）",
                financial="东方财富港股财报",
                valuation="东方财富港股估值快照",
                news="东方财富关键词新闻检索",
            )
        if kwargs.get("market") == "us":
            labels.update(
                price="新浪美股行情（前复权）",
                financial="东方财富美股F10",
                valuation="百度美股估值",
                news="东方财富关键词新闻检索",
            )
        if data_type == "index_price":
            return self._fetch_index_price(symbol, kwargs.get("index_style", "broad")), self.get_collection_error()
        if data_type == "index_valuation":
            return self._fetch_index_valuation(symbol), self.get_collection_error()
        if data_type == "index_capital_flow":
            style = kwargs.get("index_style", "broad") if kwargs.get("market", "a-shares") == "a-shares" else "overseas"
            return self._fetch_capital_flow(symbol, style, kwargs.get("as_of")), self.get_collection_error()
        if data_type == "index_macro":
            return self._fetch_index_macro(
                symbol, kwargs.get("index_style", "broad"), kwargs.get("market", "a-shares"),
            ), self.get_collection_error()
        if data_type == "index_sentiment":
            return self._fetch_index_sentiment(symbol, kwargs.get("market", "a-shares"), kwargs.get("name", "")), self.get_collection_error()
        try:
            method = getattr(self, f"_fetch_{data_type}", None)
            if method is None:
                logger.warning(f"AkShare 不支持数据类型: {data_type}")
                return [], self.get_collection_error()
            results = method(symbol, **kwargs)
            for item in results:
                if not getattr(item, "_data_source", ""):
                    item._data_source = labels.get(data_type, "AkShare")
            return results, self.get_collection_error()
        except Exception as e:
            logger.error(f"AkShare 数据获取失败 {symbol}/{data_type}: {e}")
            self._record_collection_error(labels.get(data_type, "AkShare"), e)
            return [], self.get_collection_error()


    def _fetch_price(self, symbol: str, **kwargs) -> list[PriceData]:
        if kwargs.get("market") == "hk":
            return self._fetch_hk_price(symbol, **kwargs)
        if kwargs.get("market") == "us":
            from data.us_stock import us_history

            diagnostic = DataCollectionRecord(source="新浪/东方财富美股行情", cache_status="live")
            self._collection_diagnostics.record = diagnostic
            rows = us_history(symbol, diagnostics=diagnostic.sources)
            failures = [f"{item['source']}：{item['error']}" for item in diagnostic.sources
                        if item["status"] == "failed"]
            if failures:
                diagnostic.error_kind = "source_failed"
                diagnostic.error_message = "；".join(failures)
            return rows
        # 回测等场景可显式指定起止日期（YYYYMMDD）；未完整指定时保持默认近一年（days=365）行为
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        today = datetime.now().astimezone().date()
        if start_date is None or end_date is None:
            days = kwargs.get("days", 365)  # 近一年交易日，覆盖完整行情周期
            start_date = start_date or (today - timedelta(days=days)).strftime("%Y%m%d")
            end_date = end_date or today.strftime("%Y%m%d")
        start, end = _parse_date(start_date), min(_parse_date(end_date), today)
        minimum_end = _parse_date(kwargs["minimum_end_date"]) if kwargs.get("minimum_end_date") is not None else None
        minimum_points = max(60, int(kwargs.get("minimum_points", 60)))

        def _parse(df, source_label: str) -> list[PriceData]:
            rows: dict[date, PriceData] = {}
            conflicts: set[date] = set()
            for _, row in df.iterrows():
                try:
                    date_val = row.get("date", row.get("日期"))
                    open_val = row.get("open", row.get("开盘"))
                    high_val = row.get("high", row.get("最高"))
                    low_val = row.get("low", row.get("最低"))
                    close_val = row.get("close", row.get("收盘"))
                    vol_val = row.get("volume", row.get("成交量"))
                    period = _parse_date(date_val)
                    values = [finite_number(value) for value in (open_val, high_val, low_val, close_val, vol_val)]
                    if any(value is None for value in values) or not start <= period <= end:
                        continue
                    opening, high, low, close_f, volume = cast(list[float], values)
                    if (min(opening, high, low, close_f) <= 0 or volume < 0
                            or not low <= min(opening, close_f) <= max(opening, close_f) <= high):
                        continue
                    # 涨跌幅：优先取源数据列（东方财富），缺失/坏值则按前收盘计算（新浪源）。
                    # 解析隔离在独立 try 中，坏 pct 值不连累整行 OHLCV 数据。
                    pct_raw = row.get("涨跌幅", row.get("pct_chg"))
                    change_pct = finite_number(pct_raw)
                    item = PriceData(
                        symbol=symbol,
                        trade_date=period,
                        open=opening,
                        high=high,
                        low=low,
                        close=close_f,
                        volume=int(volume),
                        change_pct=round(change_pct, 2) if change_pct is not None else None,
                    )
                    item._data_source = source_label
                    previous = rows.get(period)
                    if previous is not None:
                        if (previous.open, previous.high, previous.low, previous.close, previous.volume) != (
                                item.open, item.high, item.low, item.close, item.volume):
                            conflicts.add(period)
                        continue
                    rows[period] = item
                except (ValueError, KeyError, TypeError, OverflowError) as e:
                    logger.warning(f"跳过异常行情数据行: {e}")
            if conflicts:
                raise ValueError("同一日期存在冲突行情，拒收该来源整段历史")
            results = list(rows.values())
            # 保留来源原有顺序；异常行和重复行不再影响前收盘参照。
            for previous, current in pairwise(results):
                if current.change_pct is None:
                    current.change_pct = round((current.close / previous.close - 1) * 100, 2)
            return results

        return select_history([
            ("新浪行情（前复权）", lambda: _parse(_ak_daily(
                symbol=symbol, start_date=start_date, end_date=end_date, adjust="qfq"), "新浪行情（前复权）")),
            ("东方财富行情（前复权）", lambda: _parse(_ak_hist(
                symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="qfq"),
                "东方财富行情（前复权）")),
        ], self._append_collection_error, minimum=minimum_points,
            validate=(lambda rows: max(row.trade_date for row in rows) >= minimum_end)
            if minimum_end is not None else None)

    def _fetch_financial(self, symbol: str, **kwargs) -> list[FinancialData]:
        if kwargs.get("market") == "hk":
            return self._fetch_hk_financial(symbol)
        if kwargs.get("market") == "us":
            from data.us_stock import us_financials

            return us_financials(symbol, refresh_cache=bool(kwargs.get("refresh_cache", False)))
        from data.a_financial_policy import (
            DEPENDENCIES,
            SNAPSHOT_FIELDS,
            annotate_selection,
            package_complete,
        )
        from data.a_financial_sources import (
            fetch_eastmoney_financial,
            fetch_sina_financial,
        )
        from data.financial_selection import FinancialPackage, select_financial

        observed_periods: set[date] = set()

        def complete(package: FinancialPackage) -> bool:
            # 已观察到的历史不能因备用响应截短而被静默丢弃；范围仍为原最多十二期。
            observed_periods.update(row.fiscal_quarter for row in package.rows)
            window = set(sorted(observed_periods)[-12:])
            return window <= {row.fiscal_quarter for row in package.rows} and package_complete(package)

        def primary() -> FinancialPackage:
            return FinancialPackage("同花顺财报", self._fetch_ths_financial(symbol, **kwargs), {})

        selection = select_financial(
            [("同花顺财报", primary), ("新浪财报", lambda: fetch_sina_financial(symbol)),
             ("东方财富财报", lambda: fetch_eastmoney_financial(symbol))],
            symbol=symbol, required=SNAPSHOT_FIELDS, completeness=complete,
            dependencies=DEPENDENCIES, on_error=self._append_collection_error,
        )
        if (selection.package is not None and selection.package.source != "同花顺财报"
                and self.get_collection_error() is None):
            self._collection_diagnostics.record = DataCollectionRecord(
                source="同花顺财报", error_kind="incomplete",
                error_message="同花顺财报：字段或历史不足，采用备用财务来源",
            )
        return annotate_selection(selection)

    def _fetch_ths_capex(self, symbol: str) -> dict[date, float]:
        """同花顺现金支出按正数记录；缺项不填零。"""
        try:
            frame: Any = ak.stock_financial_cash_ths(symbol=symbol, indicator="按报告期")
            output = {}
            for _, row in frame.iterrows():
                period = _parse_date(str(row.get("报告期"))[:10])
                value = parse_cn_number(row.get("购建固定资产、无形资产和其他长期资产支付的现金"))
                if value is not None:
                    output[period] = abs(value)
            return output
        except Exception as error:  # 第三方采集边界，失败保留缺失
            logger.warning("同花顺资本开支获取失败 %s: %s", symbol, error)
            return {}

    def _fetch_ths_cashflow(self, symbol: str) -> dict[date, float]:
        """同源按报告期取直接披露经营现金流；失败返回空映射，不伪造期间证据。"""
        try:
            cash_df: Any = ak.stock_financial_cash_ths(symbol=symbol, indicator="按报告期")
        except AttributeError:
            logger.warning("同花顺现金流量表接口不可用")
            return {}
        except Exception as error:  # 第三方接口隔离；akshare.py 已豁免 BLE001
            logger.warning("同花顺现金流量表获取失败，保留原推算现金流: %s", error)
            return {}
        if not hasattr(cash_df, "get"):
            logger.warning("同花顺现金流量表响应形态异常")
            return {}
        dates = cash_df.get("报告期")
        values = cash_df.get("*经营活动产生的现金流量净额")
        if dates is None or values is None:
            logger.warning("同花顺现金流量表缺少报告期或经营活动现金流列")
            return {}
        output: dict[date, float] = {}
        conflicts: set[date] = set()
        for period, value in zip(dates, values, strict=False):
            try:
                day = datetime.strptime(str(period)[:10], "%Y-%m-%d").astimezone().date()
            except (TypeError, ValueError):
                continue
            amount = parse_cn_number(value)
            if amount is None or day in conflicts:
                continue
            if day in output and output[day] != amount:
                logger.warning("同花顺现金流量表重复报告期冲突: %s", day)
                output.pop(day, None)
                conflicts.add(day)
                continue
            output[day] = amount
        return output

    def _fetch_ths_financial(self, symbol: str, **kwargs) -> list[FinancialData]:
        """原同花顺主源采集与算法原样保留，外层仅负责主备选择。"""
        from data.ths_financial import fetch_ths_native

        native = fetch_ths_native(symbol)
        if native:
            return native
        logger.warning("同花顺新版财务摘要不可用，回退旧摘要；缩写金额不参与精确计算")
        return self._fetch_ths_legacy_financial(symbol, **kwargs)

    def _fetch_ths_legacy_financial(self, symbol: str, **kwargs) -> list[FinancialData]:
        """旧接口降级保留原始展示值，精度证据不足不升级。"""
        df: Any = ak.stock_financial_abstract_ths(symbol=symbol)

        # 从资产负债表端点补充 total_equity / total_assets（同花顺源，非东方财富）
        # 新版长表优先（含其他权益工具 → 普通股东权益 common_equity，PB 口径对齐市场惯例）
        balance_map: dict[str, tuple[float | None, float | None, float | None]] = {}
        try:
            balance_map = _parse_debt_new(ak.stock_financial_debt_new_ths(symbol=symbol))
        except Exception:
            logger.debug("新版长表资产负债表接口失败，尝试旧版接口")
            try:
                bs_df: Any = ak.stock_financial_debt_ths(symbol=symbol)
                for _, row in bs_df.iterrows():
                    period_str = str(row.get("报告期", ""))
                    try:
                        period_date = datetime.strptime(period_str, "%Y-%m-%d").astimezone().date().isoformat()
                    except ValueError:
                        continue
                    equity = parse_cn_number(row.get("*所有者权益（或股东权益）合计"))
                    assets = parse_cn_number(row.get("*资产合计"))
                    # 若简化版字段为空，尝试 "所有者权益（或股东权益）合计"（无星号版本）
                    if equity is None:
                        equity = parse_cn_number(row.get("所有者权益（或股东权益）合计"))
                    if assets is None:
                        assets = parse_cn_number(row.get("资产合计"))
                    # 旧表无法解析其他权益工具，common_equity 留空（PB 回退 total_equity）
                    balance_map[period_date] = (equity, assets, None)
            except Exception:
                logger.debug("资产负债表数据获取失败，将使用利润表数据")

        results = []
        periods = df.get("报告期", [])
        revenues = df.get("营业总收入", [])
        profits = df.get("净利润", [])
        deducted_profits = df.get("扣非净利润", [])
        roe_list = df.get("净资产收益率", [])  # ROE（%）
        gross_margins = df.get("销售毛利率", [])  # 销售毛利率（%）
        cash_flow_per_share = df.get("每股经营现金流", [])
        basic_eps_list = df.get("基本每股收益", [])  # 用于反推总股本

        # 取最近 12 期数据（数据按时间升序排列，最新在末尾）
        total = len(periods)
        cash_flow_map = (self._fetch_ths_cashflow(symbol)
                         if total and kwargs.get("market", "a-shares") == "a-shares" else {})
        capex_map = self._fetch_ths_capex(symbol) if total else {}
        start = max(0, total - 12)
        for idx in range(start, total):
            try:
                period_str = str(periods.iloc[idx] if hasattr(periods, 'iloc') else periods[idx])
                try:
                    fiscal_date = datetime.strptime(period_str, "%Y-%m-%d").astimezone().date()
                except ValueError:
                    fiscal_date = datetime.strptime(period_str, "%Y%m%d").astimezone().date()

                revenue = parse_cn_number(revenues.iloc[idx] if hasattr(revenues, 'iloc') else revenues[idx]) if idx < len(revenues) else None
                net_profit = parse_cn_number(profits.iloc[idx] if hasattr(profits, 'iloc') else profits[idx]) if idx < len(profits) else None
                deducted = parse_cn_number(deducted_profits.iloc[idx] if hasattr(deducted_profits, 'iloc') else deducted_profits[idx]) if idx < len(deducted_profits) else None

                # 净资产收益率 — 源数据为百分比（如 -7.48 / 12.5），转为小数
                roe_raw = parse_cn_number(roe_list.iloc[idx] if hasattr(roe_list, 'iloc') else roe_list[idx]) if idx < len(roe_list) else None
                roe = _normalize_percentage(roe_raw)

                # 销售毛利率 — 源数据为百分比，不能误用销售净利率。
                gm_raw = parse_cn_number(gross_margins.iloc[idx] if hasattr(gross_margins, 'iloc') else gross_margins[idx]) if idx < len(gross_margins) else None
                gross_margin = _normalize_percentage(gm_raw)

                # 每股经营现金流 × 总股本 → 经营现金流总额
                ocf_per_share = parse_cn_number(cash_flow_per_share.iloc[idx] if hasattr(cash_flow_per_share, 'iloc') else cash_flow_per_share[idx]) if idx < len(cash_flow_per_share) else None
                basic_eps = parse_cn_number(basic_eps_list.iloc[idx] if hasattr(basic_eps_list, 'iloc') else basic_eps_list[idx]) if idx < len(basic_eps_list) else None
                ocf = None
                if ocf_per_share is not None and net_profit is not None and basic_eps is not None and basic_eps > 0:
                    total_shares = net_profit / basic_eps
                    ocf = ocf_per_share * total_shares

                # 从资产负债表映射中获取净资产、普通股东权益和总资产
                date_key = fiscal_date.isoformat()
                total_equity, total_assets, common_equity = balance_map.get(
                    date_key, (None, None, None))

                # 境内定期财报以人民币列报；契约只绑定此 A 股来源，不推广到港股或模型默认值。
                source_row = df.iloc[idx]
                currency_hints = [hint for key in ("币种", "currency", "CURRENCY", "CURRENCY_CODE")
                                  for hint in (source_row.get(key), df.attrs.get(key))
                                  if hint is not None and not (isinstance(hint, str) and not hint.strip())]
                currencies = {FinancialData.normalize_report_currency(hint) for hint in currency_hints}
                if kwargs.get("market", "a-shares") == "a-shares":
                    currency = "CNY" if currencies <= {"CNY", "RMB"} else ""
                    if not currency:
                        logger.warning("A 股财报币种不符合人民币列报口径 %s/%s，保留数值但不作金额趋势比较",
                                       symbol, fiscal_date)
                else:
                    currency = next(iter(currencies)) if len(currencies) == 1 else ""

                period_start = date(fiscal_date.year, 1, 1) if (fiscal_date.month, fiscal_date.day) in {
                    (3, 31), (6, 30), (9, 30), (12, 31)} else None
                direct_ocf = cash_flow_map.get(fiscal_date)
                if direct_ocf is not None:
                    ocf = direct_ocf
                    reported = period_start is not None and currency == "CNY"
                    cash_flow_basis = "reported" if reported else "unknown"
                    cash_flow_period_start = period_start if reported else None
                else:
                    cash_flow_basis = "estimated" if ocf is not None else "unknown"
                    cash_flow_period_start = None

                results.append(FinancialData(
                    symbol=symbol,
                    fiscal_quarter=fiscal_date,
                    revenue=revenue,
                    net_profit=net_profit,
                    deducted_net_profit=deducted,
                    total_assets=total_assets,
                    total_equity=total_equity,
                    common_equity=common_equity,
                    operating_cash_flow=ocf,
                    capital_expenditure=capex_map.get(fiscal_date),
                    free_cash_flow=(ocf - capex_map[fiscal_date] if ocf is not None and fiscal_date in capex_map else None),
                    operating_cash_flow_per_share=ocf_per_share,
                    roe=roe,
                    gross_margin=gross_margin,
                    gross_margin_basis=_THS_GROSS_MARGIN_BASIS if gross_margin is not None else "",
                    basic_eps=basic_eps,
                    period_start=period_start,
                    period_basis="cumulative",
                    reporting_basis="同花顺财务摘要-按报告期",
                    report_currency=currency,
                    cash_flow_basis=cash_flow_basis,
                    cash_flow_period_start=cash_flow_period_start,
                ))
            except (ValueError, IndexError, TypeError) as e:
                logger.warning(f"跳过异常财务数据行 {idx}: {e}")
        from data.ths_financial import attach_positions, source_precision

        for row in results:
            idx = next((i for i in range(total)
                        if str(periods.iloc[i] if hasattr(periods, "iloc") else periods[i]).replace("-", "")
                        == row.fiscal_quarter.strftime("%Y%m%d")), None)
            row.precision_version = 1
            row.net_margin_basis = "旧摘要未取得已验证净利率"
            for field, column in (("revenue", revenues), ("net_profit", profits), ("deducted_net_profit", deducted_profits)):
                if idx is not None and idx < len(column):
                    row.field_precision[field] = source_precision(column.iloc[idx] if hasattr(column, "iloc") else column[idx])
            row.field_precision.update({field: "rounded" for field in
                ("operating_cash_flow", "capital_expenditure", "free_cash_flow")
                if getattr(row, field) is not None})
            if row.cash_flow_basis == "estimated" and row.operating_cash_flow is not None:
                row.field_precision["operating_cash_flow"] = "estimated"
            row.field_precision.update({field: "source_native" for field in
                ("roe", "gross_margin", "basic_eps", "operating_cash_flow_per_share")
                if getattr(row, field) is not None})
            attach_positions(row)
        return results

    def _fetch_valuation(self, symbol: str, **kwargs) -> list[ValuationData]:
        """估值：腾讯快照为主，东财日频最新行和百度曲线为备源。"""
        if kwargs.get("market") == "hk":
            return self._fetch_hk_valuation(symbol)
        if kwargs.get("market") == "us":
            from data.us_stock import us_valuation
            return us_valuation(symbol)
        import requests as _req

        from data.a_valuation_history import load_history
        from utils.numbers import is_positive_finite

        tx_symbol = f"sh{symbol}" if symbol.startswith("6") else f"sz{symbol}"
        pe = pb = None
        pe_as_of = pb_as_of = None
        tencent_failed = False
        try:
            response = _req.get(f"https://qt.gtimg.cn/q={tx_symbol}", timeout=10)
            fields = response.text.split("~")
            if len(fields) < 47:
                raise ValueError(f"腾讯快照字段数异常: {len(fields)}")
            pe = float(fields[39]) if fields[39] else None
            pb = float(fields[46]) if fields[46] else None
            stamp = fields[30]
            if len(stamp) == 14 and stamp.isdigit():
                candidate = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(
                    tzinfo=timezone(timedelta(hours=8))).date()
                if candidate <= datetime.now(timezone(timedelta(hours=8))).date():
                    pe_as_of = candidate if pe is not None else None
                    pb_as_of = candidate if pb is not None else None
        except Exception as error:  # 腾讯快照边界，继续尝试东财
            tencent_failed = True
            self._record_collection_error("腾讯估值快照", error)

        pe_source = "腾讯估值快照" if pe is not None else ""
        pb_source = "腾讯估值快照" if pb is not None else ""
        history_result = load_history(symbol, refresh=bool(kwargs.get("refresh_cache", False)))
        history = history_result.value or []
        latest = history[-1] if history else None
        notes: list[str] = []
        if tencent_failed:
            notes.append("腾讯估值快照失败，继续尝试东财日频最新行与百度曲线")
        if history_result.record.cache_status == "stale":
            notes.append(history_result.record.error_message)
        if history_result.value is None:
            notes.append(f"东财日频估值历史不可用：{history_result.record.error_message or '未取得数据'}")
        if latest is not None:
            if pe is None and latest.pe_ttm is not None:
                pe, pe_as_of = latest.pe_ttm, latest.trade_date
                pe_source = "东财日频估值历史最新行"
            if pb is None and latest.pb is not None:
                pb, pb_as_of = latest.pb, latest.trade_date
                pb_source = "东财日频估值历史最新行"

        # 东财历史不可用时，保留百度近一年曲线作为历史与快照末值兜底。
        baidu_curves: dict[str, dict[date, float]] = {}
        for metric, field in (("市盈率(TTM)", "pe"), ("市净率", "pb")):
            recent_start = datetime.now().astimezone().date() - timedelta(days=365)
            if (pe if field == "pe" else pb) is not None and any(row.trade_date >= recent_start and is_positive_finite(getattr(row, "pe_ttm" if field == "pe" else "pb")) for row in history):
                continue
            try:
                points = {day: value for day, value in
                          baidu_metric(symbol, metric, market="ab").items()
                          if day <= datetime.now().astimezone().date() and is_positive_finite(value)}
                if not points:
                    continue
                baidu_curves[field] = points
                day = max(points)
                if field == "pe" and pe is None:
                    pe, pe_as_of = points[day], day
                    pe_source = "百度曲线末值（非实时快照）"
                if field == "pb" and pb is None:
                    pb, pb_as_of = points[day], day
                    pb_source = "百度曲线末值（非实时快照）"
            except Exception as error:  # 百度网络边界
                self._record_collection_error(f"百度A股{metric}", error)
                notes.append(f"{metric} 百度曲线不可用")
        if pe is None and pb is None:
            return []

        cutoff = datetime.now().astimezone().date()
        extended_pe: dict[date, float] = {}
        extended_pb: dict[date, float] = {}
        pe_history: dict[date, float] = {}
        pb_history: dict[date, float] = {}
        if history:
            start_long = cutoff - timedelta(days=1827)
            extended_pe = {row.trade_date: float(row.pe_ttm) for row in history
                           if start_long <= row.trade_date <= cutoff
                           and row.pe_ttm is not None and is_positive_finite(row.pe_ttm)}
            extended_pb = {row.trade_date: float(row.pb) for row in history
                           if start_long <= row.trade_date <= cutoff
                           and row.pb is not None and is_positive_finite(row.pb)}
            short_start = cutoff - timedelta(days=365)
            pe_history = {day: value for day, value in extended_pe.items() if day >= short_start}
            pb_history = {day: value for day, value in extended_pb.items() if day >= short_start}
            notes.append("东财日频估值历史：2018 年至今，PE/PB/PS 分别标注日期；PS 仅展示")
            if latest is not None and latest.total_shares:
                notes.append(f"东财总股本：{latest.total_shares:.0f}")
        else:
            pe_history = baidu_curves.get("pe", {})
            pb_history = baidu_curves.get("pb", {})
            if pe_history or pb_history:
                notes.append("百度近一年历史：各指标按自身有效日期计算")

        metric_sources = {key: "东财日频估值历史" if history else "百度实测估值（近一年）"
                          for key, curve in (("pe", pe_history), ("pb", pb_history)) if curve}
        if not pe_history:
            pe_history = baidu_curves.get("pe", {})
            if pe_history:
                metric_sources["pe"] = "百度实测估值（近一年）"
        if not pb_history:
            pb_history = baidu_curves.get("pb", {})
            if pb_history:
                metric_sources["pb"] = "百度实测估值（近一年）"
        has_extended = bool(extended_pe or extended_pb)
        known_dates = [day for day in (pe_as_of, pb_as_of, latest.trade_date if latest else None)
                       if day is not None]
        from data.dividend_yield import a_share_dividend_yield

        dividend = a_share_dividend_yield(symbol)
        if dividend.value is None:
            notes.append(f"股息率：{dividend.reason}")
        item = ValuationData(
            symbol=symbol,
            date=max(known_dates) if known_dates else datetime.now().astimezone().date(),
            pe_ttm=pe, pb=pb,
            pe_as_of=pe_as_of, pb_as_of=pb_as_of,
            pe_source=pe_source, pb_source=pb_source, history_metric_sources=metric_sources,
            ps_ttm=(latest.ps_ttm if latest and is_positive_finite(latest.ps_ttm) else None),
            ps_as_of=(latest.trade_date if latest and is_positive_finite(latest.ps_ttm) else None),
            ps_source="东财日频估值历史-市销率（口径未声明）" if latest and is_positive_finite(latest.ps_ttm) else "",
            dividend_yield=dividend.value, dividend_yield_as_of=dividend.as_of,
            dividend_yield_source=dividend.source, dividend_yield_basis=dividend.basis,
            total_shares=latest.total_shares if latest else None,
            total_shares_as_of=latest.trade_date if latest and latest.total_shares is not None else None,
            total_shares_source="东财日频估值历史" if latest and latest.total_shares is not None else "",
            market_cap=latest.market_cap if latest else None,
            market_cap_as_of=latest.trade_date if latest else None,
            pe_history=pe_history, pb_history=pb_history,
            history_source=("东财日频估值历史" if has_extended else
                            "百度实测估值（近一年）" if (pe_history or pb_history) else ""),
            history_window_days=1827 if has_extended else 365,
            history_window_label="近五年" if has_extended else "近一年",
            extended_pe_history=extended_pe, extended_pb_history=extended_pb,
            valuation_notes=notes,
            cache_degraded=history_result.record.cache_status == "stale",
        )
        item._data_source = "；".join(dict.fromkeys(filter(None, [
            "腾讯估值快照" if not tencent_failed else "",
            "东财日频估值历史" if latest else "",
            "百度实测估值" if baidu_curves else "",
        ])))
        return [item]

    def _fetch_hk_price(self, symbol: str, **kwargs) -> list[PriceData]:
        code = symbol.removesuffix(".HK")
        today = datetime.now().astimezone().date()
        start = _parse_date(kwargs.get("start_date") or (today - timedelta(days=365)))
        end = min(_parse_date(kwargs.get("end_date") or today), today)
        minimum_end = _parse_date(kwargs["minimum_end_date"]) if kwargs.get("minimum_end_date") is not None else None
        minimum_points = max(60, int(kwargs.get("minimum_points", 60)))
        if start > end:
            raise ValueError("港股行情起始日期晚于结束日期")
        if len(code) != 5 or not code.isdigit():
            raise ValueError("港股代码必须为五位数字")

        def parse(lines, source: str, eastmoney: bool = False) -> list[PriceData]:
            rows: dict[date, PriceData] = {}
            conflicts: set[date] = set()
            for line in lines:
                try:
                    fields = line.split(",") if eastmoney else line
                    period = _parse_date(fields[0])
                    values = [finite_number(value) for value in fields[1:6]]
                    if len(values) != 5 or any(value is None for value in values) or not start <= period <= end:
                        continue
                    opening, close, high, low, volume = cast(list[float], values)
                    if (min(opening, close, high, low) <= 0 or volume < 0
                            or not low <= min(opening, close) <= max(opening, close) <= high):
                        continue
                    item = PriceData(symbol=symbol, trade_date=period, open=opening, high=high,
                        low=low, close=close, volume=int(volume),
                        change_pct=finite_number(fields[8]) if eastmoney and len(fields) > 8 else None)
                    item._data_source = source
                    previous = rows.get(period)
                    if previous is not None and (
                            previous.open, previous.high, previous.low, previous.close, previous.volume) != (
                            item.open, item.high, item.low, item.close, item.volume):
                        conflicts.add(period)
                        continue
                    rows[period] = item
                except (ValueError, TypeError, IndexError, AttributeError) as error:
                    logger.debug("跳过港股异常行情行: %s", error)
            if conflicts:
                raise ValueError("同一日期存在冲突港股行情，拒收该来源整段历史")
            ordered = [rows[key] for key in sorted(rows)]
            if not eastmoney:
                for previous, current in pairwise(ordered):
                    current.change_pct = round((current.close / previous.close - 1) * 100, 2)
            return ordered

        start_text, end_text, limit = start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), (end - start).days + 1
        return select_history([
            ("腾讯港股行情（不复权）", lambda: parse(tencent_history(code, start_text, end_text, limit),
                                                    "腾讯港股行情（不复权）")),
            ("东方财富港股行情（不复权）", lambda: parse(daily_history(code, start_text, end_text, limit),
                                                        "东方财富港股行情（不复权）", True)),
        ], self._append_collection_error, minimum=minimum_points,
            validate=(lambda rows: max(row.trade_date for row in rows) >= minimum_end)
            if minimum_end is not None else None)

    def _fetch_hk_financial(self, symbol: str) -> list[FinancialData]:
        from data.financial_selection import FinancialPackage, select_financial
        from data.hk_financial_policy import (
            HK_DEPENDENCIES,
            HK_REQUIRED_FIELDS,
            annotate_selection,
            package_complete,
        )

        selection = select_financial(
            [("东方财富港股财报", lambda: FinancialPackage(
                "东方财富港股财报", self._fetch_hk_eastmoney_financial(symbol), {},
            ))],
            symbol=symbol,
            required=HK_REQUIRED_FIELDS,
            completeness=package_complete,
            dependencies=HK_DEPENDENCIES,
            on_error=self._append_collection_error,
        )
        return annotate_selection(selection)

    def _fetch_hk_eastmoney_financial(self, symbol: str) -> list[FinancialData]:
        code = symbol.removesuffix(".HK")
        income = financial_statement(code, "利润表")
        def aggregate(records: list[dict[str, Any]]) -> dict[date, dict[str, float]]:
            """同一日期多科目长表聚合；不求和重复科目，不让空值覆盖有效值。"""
            periods: dict[date, dict[str, float]] = {}
            for row in records:
                try:
                    if (str(row.get("SECUCODE", symbol)) != symbol
                            or str(row.get("SECURITY_CODE", code)) != code):
                        continue
                    period = _parse_date(row.get("REPORT_DATE"))
                    item = str(row.get("STD_ITEM_CODE", ""))
                    amount = finite_number(row.get("AMOUNT"))
                    if item and amount is not None:
                        periods.setdefault(period, {}).setdefault(item, amount)
                except (ValueError, TypeError) as error:
                    logger.debug("跳过港股异常财报行: %s", error)
            return periods

        income_periods = aggregate(income)
        margins = gross_margins(income, symbol)
        statement_rows: dict[str, list[dict[str, Any]]] = {"利润表": income}

        def period_metadata(records: list[dict[str, Any]]) -> dict[date, tuple[date | None, str]]:
            """只采用来源明确给出的期间起点与币种；冲突时不生成趋势口径。"""
            grouped: dict[date, set[tuple[date | None, str]]] = {}
            for row in records:
                if str(row.get("SECUCODE", symbol)) != symbol or str(row.get("SECURITY_CODE", code)) != code:
                    continue
                try:
                    end = _parse_date(row.get("REPORT_DATE"))
                    start = _parse_date(row["START_DATE"]) if row.get("START_DATE") else None
                    if start is not None and start > end:
                        start = None
                    currency = FinancialData.normalize_report_currency(row.get("CURRENCY_CODE") or row.get("CURRENCY"))
                    grouped.setdefault(end, set()).add((start, currency))
                except (TypeError, ValueError):
                    logger.debug("港股财报期间元信息无效，不生成比较口径")
            return {end: next(iter(values)) if len(values) == 1 else (None, "")
                    for end, values in grouped.items()}

        def supplement(statement: str) -> dict[date, dict[str, float]]:
            try:
                records = financial_statement(code, statement)
                statement_rows[statement] = records
                return aggregate(records)
            except Exception as error:  # 第三方补充报表隔离；本文件已豁免 BLE001
                logger.warning("港股%s获取失败，仅保留其他已获取财务字段: %s", statement, error)
                previous = self.get_collection_error()
                self._record_collection_error(f"东方财富港股财报（{statement}）", error)
                diagnostic = self.get_collection_error()
                if diagnostic is not None:
                    messages = [previous.error_message] if previous is not None else []
                    messages.append(f"{statement}：{diagnostic.error_message}")
                    self._collection_diagnostics.record = diagnostic.model_copy(update={
                        "error_message": "；".join(messages),
                    })
                return {}

        assets, cash_flows = supplement("资产负债表"), supplement("现金流量表")
        income_meta = period_metadata(income)
        from data.hk_currency_evidence import (
            load_indicator_periods,
            verify_cash_flow,
            verify_period,
        )

        try:
            indicators = load_indicator_periods(code)
        except Exception as error:  # 第三方指标边界，失败不阻断三表
            indicators = {}
            logger.warning("港股分析指标获取失败 %s: %s", code, type(error).__name__)
            previous = self.get_collection_error()
            self._append_collection_error("东方财富港股分析指标", error)
            current = self.get_collection_error()
            if previous is not None and current is not None:
                self._collection_diagnostics.record = previous.model_copy(update={
                    "error_message": current.error_message,
                })

        cash_meta = period_metadata(statement_rows.get("现金流量表", []))
        balance_meta = period_metadata(statement_rows.get("资产负债表", []))
        directory_meta: dict[date, tuple[date | None, str, str]] = {}
        try:
            grouped: dict[date, set[tuple[date | None, str, str]]] = {}
            for entry in financial_report_directory(code):
                if entry.get("SECUCODE") != symbol or entry.get("SECURITY_CODE") != code:
                    continue
                try:
                    end = _parse_date(entry.get("REPORT_DATE"))
                except (TypeError, ValueError):
                    logger.debug("港股财报目录报告日期无效，不补元信息")
                    continue
                try:
                    beginning = _parse_date(entry.get("START_DATE"))
                    if beginning > end:
                        beginning = None
                except (TypeError, ValueError):
                    logger.debug("港股财报目录期间起点无效，不补元信息")
                    beginning = None
                currency = FinancialData.normalize_report_currency(entry.get("CURRENCY"))
                standard = entry.get("ACCOUNT_STANDARD")
                standard = standard.strip() if isinstance(standard, str) else ""
                if standard.casefold() in {"--", "-", "n/a", "na", "nan", "null", "none", "unknown",
                                          "未知", "未记录", "暂无数据"}:
                    standard = ""
                grouped.setdefault(end, set()).add((beginning, currency, standard))
            directory_meta = {end: next(iter(values)) if len(values) == 1 else (None, "", "")
                              for end, values in grouped.items()}
        except Exception as error:  # 第三方目录隔离；失败不丢弃已获取的三张报表
            logger.warning("港股财报目录获取失败，保留原始报表与未知元信息: %s", error)
            self._append_collection_error("东方财富港股财报（报告目录）", error)
        result = []
        for period, row in sorted(income_periods.items()):
            bs = assets.get(period, {})
            cf = cash_flows.get(period, {})
            start, currency = income_meta.get(period, (None, ""))
            cf_start, cf_currency = cash_meta.get(period, (None, ""))
            reporting_basis = "东方财富港股财报-报告期"
            disclosure_currency = ""
            if period in directory_meta:
                directory_start, directory_currency, standard = directory_meta[period]
                if start is not None and start == directory_start and standard:
                    # 目录列报币种只作披露元信息，不为 AMOUNT 作币种或未换汇背书。
                    disclosure_currency = directory_currency
                    reporting_basis += f"-{standard}"
                else:
                    # 冲突目录不能覆盖源字段，也不能悄悄回退为可比较口径。
                    currency, reporting_basis = "", ""
            # 银行与普通企业的科目位置不同，按整期模板选择，不逐字段跨模板补值。
            standard_template = any(key.startswith("004") for key in row)
            bank_template = any(key.startswith("001") for key in row)
            if standard_template and bank_template:
                logger.warning("港股利润表科目模板冲突 %s/%s，跳过该期", symbol, period)
                continue
            if bank_template:
                revenue, profit, eps = "001003999", "001025002", "001027002"
                total_assets, total_equity, common_equity = "001001999", "001011999", "001009999"
            else:
                revenue, profit, eps = "004001001", "004025002", "004027002"
                total_assets, total_equity, common_equity = "004009999", "004036999", "004030999"
            # 营业额不是营运收入；归母权益不是包含少数股东的总权益，分别保留。
            margin = margins.get(period) if start is not None and not bank_template else None
            parts = [cf.get(key) for key in ("005005", "005007")]
            capex = sum(abs(value) for value in parts if value is not None) if any(value is not None for value in parts) and not bank_template else None
            ocf = cf.get("003999")
            indicator = indicators.get(period)
            evidence = verify_period(period, start, income, indicator, bank=bank_template)
            currency = evidence.currency if evidence.verified else ""
            verified = evidence.verified
            cf_verified = (verify_cash_flow(start, ocf, indicator, currency)[0]
                           and (not cf_currency or cf_currency == currency))

            annual_indicator = indicator if start is not None and start.month == 1 and period.month == 12 else None
            annual_roe = finite_number(annual_indicator.get("ROE_AVG")) if annual_indicator else None
            reported_annual_roe = annual_roe / 100 if annual_roe is not None else None

            result.append(FinancialData(symbol=symbol, fiscal_quarter=period,
                revenue=row.get(revenue), net_profit=row.get(profit), basic_eps=row.get(eps),
                total_assets=bs.get(total_assets), total_equity=bs.get(total_equity),
                common_equity=bs.get(common_equity), operating_cash_flow=ocf,
                capital_expenditure=capex, free_cash_flow=ocf - capex if ocf is not None and capex is not None else None,
                gross_margin=margin, gross_margin_basis="银行/保险不适用" if bank_template else GROSS_MARGIN_BASIS if margin is not None else "",
                period_start=start, period_basis="cumulative" if start is not None else "unknown",
                reporting_basis=reporting_basis, report_currency=currency,
                amount_currency_verified=verified,
                amount_currency_reason="逐期报表聚合与分析指标一致" if verified else evidence.reason,
                reported_annual_roe=reported_annual_roe,
                reported_annual_roe_period=period if reported_annual_roe is not None else None,
                reported_annual_roe_source="东方财富港股分析指标" if reported_annual_roe is not None else "",
                disclosure_currency=disclosure_currency,
                revenue_basis="银行：经营收入总额" if bank_template else "",
                cash_flow_basis="reported" if cf_verified else "unknown",
                cash_flow_period_start=start if cf_verified else cf_start))
            row_data = result[-1]
            if verified and start is not None:
                accounting = ""
                if period in directory_meta and directory_meta[period][0] == start:
                    accounting = directory_meta[period][2]
                if row_data.revenue is not None and row_data.revenue > 0 and row_data.net_profit is not None:
                    row_data.net_margin = row_data.net_profit / row_data.revenue
                    row_data.net_margin_basis = "计算值：净利润÷营业收入（报告期口径）；仅展示"
                for name, definition in (("revenue", "revenue"), ("net_profit", "parent_net_profit"),
                                         ("operating_cash_flow", "operating_cash_flow"),
                                         ("total_assets", "total_assets"), ("total_equity", "total_equity")):
                    if getattr(row_data, name) is None:
                        continue
                    instant = name in {"total_assets", "total_equity"}
                    cash_field = name == "operating_cash_flow"
                    cash_verified = cash_field and cf_verified
                    row_data.field_positions[name] = {
                        "definition": definition, "unit": "currency",
                        "currency": (currency if cash_verified else "") if cash_field
                        else balance_meta.get(period, (None, ""))[1] if instant else currency,
                        "start": (start.isoformat() if cash_verified and start else
                                  (cf_start.isoformat() if cf_start else "")) if cash_field
                        else (period if instant else start).isoformat(),
                        "end": period.isoformat(), "basis": "instant" if instant else "cumulative",
                        "scope": "consolidated", "accounting": accounting, "revision": "当前源快照",
                        "calculation": ("reported" if cash_verified else "unknown") if cash_field else "reported",
                    }
            # 此接口 AMOUNT 为未缩写数值；币种/单位与跨期修订仍由独立口径证据校验。
            result[-1].field_precision = {field: "source_native" for field in (
                "revenue", "net_profit", "basic_eps", "total_assets", "total_equity", "common_equity",
                "operating_cash_flow", "capital_expenditure", "free_cash_flow", "gross_margin")
                if getattr(result[-1], field) is not None}
        return result

    def _fetch_hk_valuation(self, symbol: str) -> list[ValuationData]:
        code = symbol.removesuffix(".HK")
        if len(code) != 5 or not code.isdigit():
            raise ValueError("港股代码必须为五位数字")
        today = datetime.now(timezone(timedelta(hours=8))).date()
        notes: list[str] = []

        pe: float | None = None
        pb: float | None = None
        pe_as_of: date | None = None
        pb_as_of: date | None = None
        market_cap = total_shares = None
        quote_date: date | None = None
        primary_failed = False
        try:
            snapshot = stock_snapshot(code)
            if snapshot.get("f57") != code:
                raise ValueError("东方财富港股估值代码不匹配")
            snapshot_pe = finite_number(snapshot.get("f164"))
            snapshot_pb = finite_number(snapshot.get("f167"))
            timestamp = finite_number(snapshot.get("f86"))
            if timestamp is None or timestamp <= 0 or (snapshot_pe is None and snapshot_pb is None):
                raise ValueError("东方财富港股估值没有合法指标或日期")
            snapshot_date = datetime.fromtimestamp(timestamp, tz=UTC).astimezone(
                timezone(timedelta(hours=8))).date()
            if snapshot_date > today:
                raise ValueError("东方财富港股估值日期晚于采集日期")
            # 全部校验通过后才接受快照，否则保持 None 以允许估值对比或曲线兜底。
            quote_date = snapshot_date
            market_cap = finite_number(snapshot.get("f116"))
            total_shares = finite_number(snapshot.get("f84"))
            market_cap = market_cap if market_cap is not None and market_cap > 0 else None
            total_shares = total_shares if total_shares is not None and total_shares > 0 else None
            pe, pb = snapshot_pe, snapshot_pb
            pe_as_of = snapshot_date if pe is not None else None
            pb_as_of = snapshot_date if pb is not None else None
        except Exception as error:  # 第三方快照隔离；本文件已豁免 BLE001
            logger.warning("东方财富港股估值快照失败: %s", error)
            self._append_collection_error("东方财富港股估值快照", error)
            notes.append("东方财富港股估值快照：采集失败")
            primary_failed = True

        comparison: dict[str, float | None] = {}
        comparison_date: date | None = None
        comparison_failed = False
        try:
            row = valuation_comparison_snapshot(code)
            comparison_date = date.fromisoformat(str(row["REPORT_DATE"])[:10])
            if comparison_date > today:
                raise ValueError("东方财富港股估值对比日期晚于采集日期")
            comparison = {
                "pe": finite_number(row.get("PE_TTM")),
                "pb": finite_number(row.get("PB_MQR")),
                "ps": finite_number(row.get("PS_TTM")),
            }
        except Exception as error:  # 第三方估值对比请求隔离
            logger.warning("东方财富港股估值对比失败: %s", error)
            self._append_collection_error("东方财富港股估值对比", error)
            comparison_failed = True
            notes.append("东财估值对比采集失败，PE(TTM)/PB/PS(TTM) 补位不可用")

        filled: list[str] = []
        if pe is None and comparison.get("pe") is not None:
            pe, pe_as_of = comparison["pe"], comparison_date
            filled.append("PE(TTM)")
        if pb is None and comparison.get("pb") is not None:
            pb, pb_as_of = comparison["pb"], comparison_date
            filled.append("PB")
        if filled:
            notes.append(f"东财估值对比补位：{'、'.join(filled)}（截至 {comparison_date}）")
        ps = comparison.get("ps")
        ps_date = comparison_date if ps is not None else None
        if not comparison_failed and ps is None:
            notes.append("PS(TTM)：东财估值对比未提供有效数值")

        # 两项请求独立，最多两个并发；诊断在调用线程写回，避免 thread-local 丢失。
        curves: dict[str, dict[date, float]] = {}
        history_failed = False
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {metric: executor.submit(baidu_metric, code, metric)
                       for metric in ("市盈率(TTM)", "市净率")}
            for metric, future in futures.items():
                try:
                    points = future.result()
                    if not points:
                        raise ValueError("百度估值没有合法指标")
                    curves[metric] = points
                except Exception as error:  # 第三方指标请求隔离
                    logger.warning("百度港股%s获取失败: %s", metric, error)
                    self._append_collection_error(f"百度港股{metric}", error)
                    notes.append(f"{metric}历史：百度近一年曲线采集失败")
                    history_failed = True
        pe_curve, pb_curve = curves.get("市盈率(TTM)", {}), curves.get("市净率", {})
        common = pe_curve.keys() & pb_curve.keys()
        if common:
            period = max(common)
        elif pe_curve or pb_curve:
            period = max(pe_curve.keys() | pb_curve.keys())
            if pe_curve and pb_curve and pe is None and pb is None:
                diagnostic = self.get_collection_error()
                if diagnostic is not None:
                    self._collection_diagnostics.record = diagnostic.model_copy(update={
                        "error_message": diagnostic.error_message + "；百度 PE/PB 无共同日期，仅保留日期较新指标",
                    })
        elif pe_as_of is not None or pb_as_of is not None:
            period = max(value for value in (pe_as_of, pb_as_of) if value is not None)
        elif ps_date is not None:
            period = ps_date
        else:
            return []

        curve_fallback = pe is None and pb is None and bool(pe_curve or pb_curve)
        if curve_fallback:
            item_date = period
        else:
            # item.date 只代表 PE/PB 快照时点；PS 日期单独展示，不能冒充 PE/PB 日期。
            snapshot_dates = [value for value in (pe_as_of, pb_as_of)
                              if value is not None]
            if snapshot_dates:
                item_date = max(snapshot_dates)
            elif ps_date is not None:
                item_date = ps_date
            else:
                item_date = period

        item = ValuationData(symbol=symbol, date=item_date, pe_ttm=pe, pb=pb,
                             pe_as_of=pe_as_of, pb_as_of=pb_as_of,
                             market_cap=market_cap, market_cap_as_of=quote_date if market_cap else None,
                             total_shares=total_shares, total_shares_as_of=quote_date if total_shares else None,
                             total_shares_source="东方财富港股快照" if total_shares else "")
        if curve_fallback:
            # 两个东财入口都未取得 PE/PB 时，才允许百度曲线末值兜底。
            item.pe_ttm = pe_curve.get(period)
            item.pb = pb_curve.get(period)
            item.pe_as_of = period if item.pe_ttm is not None else None
            item.pb_as_of = period if item.pb is not None else None
            item._data_source = "百度港股估值曲线末值（非实时快照）"
            notes.append(f"东财 PE/PB 均未取得，使用百度曲线末值（截至 {period}）")
        elif primary_failed and not comparison_failed:
            item._data_source = (
                "仅 PS" if pe is None and pb is None and ps is not None
                else "东方财富港股估值对比"
            )
        elif filled:
            item._data_source = "东方财富港股估值快照+估值对比"
        else:
            item._data_source = "东方财富港股估值快照"
        item.ps_ttm, item.ps_as_of = ps, ps_date
        item.ps_source = "东方财富港股估值对比" if ps is not None else ""
        from data.dividend_yield import hk_dividend_yield

        dividend = hk_dividend_yield(code)
        item.dividend_yield = dividend.value
        item.dividend_yield_as_of = dividend.as_of
        item.dividend_yield_source = dividend.source
        item.dividend_yield_basis = dividend.basis
        if dividend.value is None:
            notes.append(f"股息率：{dividend.reason}")
        item.pe_history, item.pb_history = pe_curve, pb_curve
        item.history_source = "百度港股估值（近一年）" if curves else ""
        if pe is not None and pb is not None and len(curves) == 2:
            try:
                start = today - timedelta(days=1827)
                last_closed = today if datetime.now(timezone(timedelta(hours=8))).hour >= 17 else today - timedelta(days=1)
                trading_days = {
                    _parse_date(quote[0])
                    for year in range(start.year, last_closed.year + 1)
                    for quote in tencent_history(code, str(max(start, date(year, 1, 1))),
                                                 str(min(last_closed, date(year, 12, 31))), 400)
                }
                windows = []
                for metric, attribute in (("市盈率(TTM)", "extended_pe_history"), ("市净率", "extended_pb_history")):
                    baseline = curves.get(metric, {})

                    def verified_curve(code, metric, *, chart_select, baseline=baseline):
                        points = baidu_metric(code, metric, chart_select=chart_select)
                        if any(points[day] != baseline[day] for day in points.keys() & baseline.keys()
                               if day < today and day in trading_days):
                            raise ValueError("百度长窗与短窗共同历史日估值不同")
                        return {day: value for day, value in points.items()
                                if start <= day <= last_closed and day in trading_days}

                    points, days, label = _hk_history_window(verified_curve, code, metric)
                    setattr(item, attribute, points)
                    if getattr(item, attribute):
                        windows.append((days, label))
                if item.extended_pe_history or item.extended_pb_history:
                    item.history_window_days, item.history_window_label = min(windows)
                    start = today - timedelta(days=item.history_window_days)
                    item.extended_pe_history = {d: v for d, v in item.extended_pe_history.items() if d >= start}
                    item.extended_pb_history = {d: v for d, v in item.extended_pb_history.items() if d >= start}
            except Exception as error:
                logger.warning("百度港股长窗估值未验证，保留近一年数据: %s", error)
                notes.append("百度长窗估值暂不可用，保留原近一年数据")
                item.extended_pe_history = {}
                item.extended_pb_history = {}
        item.valuation_notes = notes
        item.cache_degraded = (
            primary_failed or comparison_failed or history_failed or curve_fallback
        )
        if primary_failed:
            item.failed_components.append("snapshot")
        if comparison_failed:
            item.failed_components.append("comparison")
        if "市盈率(TTM)" not in curves:
            item.failed_components.append("pe_history")
        if "市净率" not in curves:
            item.failed_components.append("pb_history")
        return [item]

    def _fetch_industry(self, symbol: str, **kwargs) -> list[IndustryData]:
        if kwargs.get("market") == "us":
            from data.us_industry import us_industry_result

            result = us_industry_result(symbol, cache=kwargs.get("comparison_cache"),
                                        refresh=bool(kwargs.get("refresh_cache", False)))
            self._collection_diagnostics.record = result.record
            return [result.value] if result.value is not None else []
        from data.peer_comparison import fetch_a_industry

        return fetch_a_industry(
            symbol, cache=kwargs.get("comparison_cache"),
            refresh=bool(kwargs.get("refresh_cache", False)),
            comparison_cache_only=bool(kwargs.get("comparison_cache_only", False)),
        )

    def _fetch_news(self, symbol: str, **kwargs) -> list[NewsData]:
        """拉取近 30 天新闻和公告，附带 RawSentimentData"""
        from pandas import isna
        from pandas.api.types import is_scalar

        if kwargs.get("market") == "us":
            from data.us_stock import us_news

            return us_news(symbol, str(kwargs.get("name") or ""))
        if kwargs.get("market") == "hk":
            from data.hk_news import fetch_news

            snapshot = fetch_news(symbol, kwargs.get("name", ""))
            return [NewsData(symbol=symbol, date=snapshot.fetched_at.date(), hk_news=snapshot,
                             headlines=[item.title for item in snapshot.items])]
        from data.hk_news import HK_TZ
        from data.schemas import RawSentimentData, RawSentimentItem

        now = datetime.now(HK_TZ)
        today = now.date()
        start_date = today - timedelta(days=29)
        items = []
        seen_titles: set[tuple] = set()
        seen_urls: set[str] = set()

        def text_value(row: Any, *keys: str) -> str:
            # NaN/NA/NaT 先判缺失，不能转成文本或挡住真实后备字段。
            for key in keys:
                value = row.get(key)
                if not is_scalar(value) or isna(value):
                    continue
                text = str(value).strip()
                if text:
                    return text
            return ""

        def add_item(row: Any, source: str) -> None:
            title = text_value(row, "公告标题", "标题", "title", "新闻标题")
            raw_date = (text_value(row, "公告日期", "日期") if source == "announcement"
                        else text_value(row, "发布时间", "时间"))
            try:
                stamp = datetime.fromisoformat(str(raw_date))
                stamp = stamp.replace(tzinfo=HK_TZ) if stamp.tzinfo is None else stamp.astimezone(HK_TZ)
            except (ValueError, TypeError):
                logger.debug("跳过未知或无效日期的舆情条目：%s", title)
                return
            if not title or not start_date <= stamp.date() <= today or stamp > now:
                return
            url = text_value(row, "新闻链接", "公告链接", "网址", "链接", "url")
            key = (stamp.date(), title)
            if key in seen_titles or (url and url in seen_urls):
                return
            seen_titles.add(key)
            if url:
                seen_urls.add(url)
            # 公告接口仅有标题时保持空正文，禁止按标题补造内容。
            content = (text_value(row, "新闻内容", "内容", "content")[:500]
                       if source == "news" else "")
            media = text_value(row, "文章来源", "新闻来源", "来源")
            items.append(RawSentimentItem(title=title, source=source, publish_date=stamp.date(),
                                          content=content, url=url, media=media))

        try:
            df: Any = _ak_news(symbol)
            if df is not None and not df.empty:
                for _, row in df.head(20).iterrows():
                    add_item(row, "news")
        except Exception as error:
            logger.warning("新闻数据获取失败：%s", error)
            self._record_collection_error("东方财富新闻", error)

        try:
            notice_df: Any = ak.stock_individual_notice_report(security=symbol)
            if notice_df is not None and not notice_df.empty:
                for _, row in notice_df.head(20).iterrows():
                    add_item(row, "announcement")
                from data.a_announcements import extract_art_code, fetch_notice_content

                # 串行请求满足并发不超过三条；失败保留已有标题和链接。
                for item in [entry for entry in items if entry.source == "announcement"][:10]:
                    art_code = extract_art_code(item.url)
                    if art_code:
                        try:
                            item.content = fetch_notice_content(art_code)[:1000]
                        except Exception as error:
                            logger.debug("公告正文获取失败 %s：%s", art_code, error)
        except Exception as error:
            logger.warning("公告数据获取失败：%s", error)
            self._record_collection_error("东方财富公告", error)

        # 去重并按日期排序，最多保留 30 条
        items.sort(key=lambda x: x.publish_date, reverse=True)
        items = items[:30]

        headlines = [item.title for item in items]
        result = NewsData(symbol=symbol, date=today, headlines=headlines)

        # 把 raw_sentiment 存到 result 的额外属性
        result._raw_sentiment = RawSentimentData(symbol=symbol, fetch_date=today, items=items)
        return [result]

    # ============================================================
    # 指数数据采集
    # ============================================================

    @retry_on_network_error()
    def _fetch_index_price(self, symbol: str, index_style: str) -> list[IndexPriceData]:
        if index_style == "strategy":
            from data.index_mapping import IndexMapping
            from index.strategy_data import StrategyDataProvider

            entry = IndexMapping().lookup(symbol)
            provider = entry.provider if entry and entry.provider else "csi"
            return StrategyDataProvider().fetch_prices(symbol, provider=provider)

        actual_source = "来源未记录"
        try:
            # 宽基指数使用 stock_zh_index_daily_em（主源，东方财富）
            if index_style == "broad":
                actual_source = "东方财富指数日线"
                df: Any = ak.stock_zh_index_daily_em(symbol=symbol)
                if df is None or df.empty:
                    # 主源不可用 → 回退腾讯源（参数需 sh/sz 前缀）
                    tx_symbol = f"sz{symbol}" if symbol.startswith("399") else f"sh{symbol}"
                    actual_source = "腾讯指数日线（东方财富源无数据后回退）"
                    df: Any = ak.stock_zh_index_daily_tx(symbol=tx_symbol)
            elif index_style == "sector":
                # 行业板块指数使用申万指数接口
                actual_source = "申万指数历史"
                df: Any = ak.index_hist_sw(symbol=symbol)
            elif index_style == "overseas":
                try:
                    df = ak.index_global_hist_em(symbol=_OVERSEAS_NAME_MAP.get(symbol, symbol))
                    results = self._parse_index_prices(
                        symbol, df, "东方财富全球指数历史", eastmoney_global=True,
                    )
                except Exception as exc:
                    logger.debug("东财全球指数接口失败 %s：%s", symbol, exc)
                    results = []
                if results:
                    return results
                actual_source = "新浪全球指数历史（东方财富回退）"
                df = _fetch_overseas_index_sina(symbol)
            else:
                return []

            return self._parse_index_prices(symbol, df, actual_source)
        except Exception as e:
            logger.warning(f"获取指数 {symbol} 行情失败: {e}")
            return []

    @staticmethod
    def _parse_index_prices(
        symbol: str, df: Any, source: str, *, eastmoney_global: bool = False,
    ) -> list[IndexPriceData]:
        from data.schemas import IndexPriceData

        if df is None or df.empty:
            return []
        open_columns = ("open", "开盘", "开盘价")
        close_columns = ("close", "收盘", "收盘价")
        if eastmoney_global:
            # SPX 2026-11-17 与新浪同日核验：今开一致，最新价与历史收盘仅差两位小数舍入。
            open_columns += ("今开",)
            close_columns += ("最新价",)
        results: list[IndexPriceData] = []
        prev_close: float | None = None
        for _, row in df.iterrows():
            try:
                date_val = _row_get_any(row, "date", "日期", "trade_date", "交易日期")
                open_val = _row_get_any(row, *open_columns)
                high_val = _row_get_any(row, "high", "最高", "最高价")
                low_val = _row_get_any(row, "low", "最低", "最低价")
                close_val = _row_get_any(row, *close_columns)
                volume_val = _row_get_any(row, "volume", "成交量", default=0)
                amount_val = _row_get_any(row, "amount", "成交额")

                if date_val is None or close_val is None:
                    raise KeyError("date/close")

                close_f = float(close_val)
                # 涨跌幅：优先取源数据列（部分源/旧版 akshare 提供），缺失/坏值按前收盘计算。
                # 注：安装版 akshare 的 stock_zh_index_daily_em 在返回前丢弃承载涨跌幅
                # 的 "_" 列，腾讯源亦无涨跌幅列，故实际生效的是按前收盘计算；
                # "_" 回退仅为防御未来版本保留该列的情况。
                # 解析隔离在独立 try 中，坏 pct 值不连累整行 OHLCV 数据。
                pct_raw = _row_get_any(row, "涨跌幅", "pct_chg", "_")
                change_pct = None
                if pct_raw is not None and str(pct_raw) not in ("", "nan"):
                    try:
                        change_pct = round(float(pct_raw), 2)
                    except (ValueError, TypeError) as e:
                        logger.debug(f"指数涨跌幅解析失败，回退按前收盘计算: {e}")
                if change_pct is None and prev_close:
                    change_pct = round((close_f - prev_close) / prev_close * 100, 2)
                results.append(IndexPriceData(
                    symbol=symbol,
                    trade_date=_parse_date(date_val),
                    open=float(open_val),
                    high=float(high_val),
                    low=float(low_val),
                    close=close_f,
                    volume=int(float(volume_val or 0)),
                    turnover=float(amount_val) / 1e8 if amount_val else None,
                    change_pct=change_pct,
                ))
                prev_close = close_f
            except (ValueError, KeyError, TypeError) as e:
                logger.warning(f"跳过异常指数行情数据行: {e}")
        for item in results:
            item._data_source = source
        return results

    @retry_on_network_error()
    def _fetch_index_valuation(self, symbol: str) -> list[IndexValuationData]:
        from data.index_mapping import IndexMapping
        from index.valuation_data import CURRENT_NAMES, StrategyValuationProvider

        entry = IndexMapping().lookup(symbol)
        if symbol in CURRENT_NAMES:
            source = "中证指数官方当前估值"
            try:
                value = StrategyValuationProvider(timeout=10, request_timeout=(5, 10)).fetch_current(symbol)
            except Exception as exc:
                self._record_collection_error(source, exc)
                return []
            self._collection_diagnostics.record = make_record(source, "partial" if value else "failed",
                error_kind="" if value else "empty", error_message="" if value else "未取得有效官方估值快照")
            return [value] if value else []
        if entry is not None and entry.index_style == "overseas":
            self._collection_diagnostics.record = make_record(
                "指数估值", "failed", error_kind="not_supported", error_message="指数官方估值来源尚未核实",
            )
            return []
        if entry is None or not entry.provider:
            self._record_collection_error("指数估值", ValueError("指数官方估值来源尚未核实"))
            return []
        provider = StrategyValuationProvider(timeout=10, request_timeout=(5, 10))
        value = provider.fetch(symbol, provider=entry.provider, include_daily_pe=False)
        return [value] if value is not None else []

    @retry_on_network_error()
    def _fetch_capital_flow(self, symbol: str, index_style: str, as_of: date | None = None) -> list[CapitalFlowData]:
        if index_style not in {"broad", "sector"}:
            return []
        source = "上交所、深交所融资融券汇总（AkShare）"
        anchor = as_of or datetime.now().astimezone().date()
        start = anchor - timedelta(days=7)
        sh_balances: dict[date, float] = {}
        common: list[tuple[date, float]] = []
        try:
            sh_frame: Any = ak.stock_margin_sse(start_date=start.strftime("%Y%m%d"), end_date=anchor.strftime("%Y%m%d"))
            for row in sh_frame.to_dict("records"):
                try:
                    day = _parse_date(row.get("信用交易日期"))
                except ValueError:
                    continue
                balance = finite_number(row.get("融资余额"))
                if start <= day <= anchor and balance is not None and balance > 0:
                    sh_balances[day] = balance / 1e8
        except Exception as exc:
            self._record_collection_error(source, exc)
            return []
        for day in sorted(sh_balances, reverse=True):
            try:
                sz_frame: Any = ak.stock_margin_szse(date=day.strftime("%Y%m%d"))
                rows = sz_frame.to_dict("records")
                if len(rows) != 1:
                    continue
                row = rows[0]
                if any(_parse_date(row[key]) != day for key in ("日期", "交易日期", "信用交易日期") if key in row):
                    continue
                balance = finite_number(row.get("融资余额"))
                if balance is not None and balance > 0:
                    common.append((day, sh_balances[day] + balance))
                    if len(common) == 2:
                        break
            except ValueError as exc:
                logger.debug("深交所融资余额 %s 无可用报表: %s", day, exc)
            except Exception as exc:
                logger.warning("深交所融资余额 %s 采集失败: %s", day, exc)
                self._record_collection_error(source, exc)
                break
        if not common:
            if self.get_collection_error() is None:
                self._collection_diagnostics.record = make_record(source, "failed", error_kind="empty",
                    error_message="七日内未取得沪深同日融资余额")
            return []
        day, balance = common[0]
        change = balance - common[1][1] if len(common) == 2 else None
        self._collection_diagnostics.record = make_record(source, "complete" if change is not None else "partial")
        return [CapitalFlowData(symbol=symbol, date=day, margin_date=day.isoformat(),
                                margin_balance=balance, margin_change=change)]

    @retry_on_network_error()
    def _fetch_index_macro(self, symbol: str, index_style: str, market: str = "a-shares") -> list[MacroContext]:
        if market == "us":
            return self._fetch_us_index_macro(symbol)
        if market != "a-shares":
            return []
        if index_style == "sector":
            # sector 保留 MacroContext 实例但字段全 None
            return [MacroContext(symbol=symbol, fetch_date=datetime.now().astimezone().date())]

        result = MacroContext(symbol=symbol, fetch_date=datetime.now().astimezone().date())
        try:
            # PMI（兼容新旧 akshare：新版本降序且列为"制造业-指数"，旧版本升序且列为"制造业"）
            df_pmi: Any = ak.macro_china_pmi()
            if df_pmi is not None and not df_pmi.empty:
                if "制造业-指数" in df_pmi.columns:
                    latest = df_pmi.iloc[0]
                    pmi_val = latest.get("制造业-指数")
                else:
                    latest = df_pmi.iloc[-1]
                    pmi_val = latest.get("制造业")
                result.pmi = float(pmi_val) if pmi_val else None
                result.indicator_dates["pmi"] = str(latest.get("月份") or latest.get("日期") or "未记录")

            # Shibor（3 个月期；兼容新旧 akshare 参数）
            try:
                df_shibor: Any = ak.rate_interbank(market="上海银行间同业拆放利率", indicator="Shibor")
                three_month = df_shibor[df_shibor["期限"] == "3M"]
                if not three_month.empty:
                    result.shibor_3m = float(three_month.iloc[-1]["利率"])
                    result.indicator_dates["shibor_3m"] = str(three_month.iloc[-1].get("报告日") or three_month.iloc[-1].get("日期") or "未记录")
            except Exception:
                df_shibor: Any = ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="3月")
                if df_shibor is not None and not df_shibor.empty:
                    result.shibor_3m = float(df_shibor.iloc[-1]["利率"])
                    result.indicator_dates["shibor_3m"] = str(df_shibor.iloc[-1].get("报告日") or df_shibor.iloc[-1].get("日期") or "未记录")

            # USD/CNY（仅海外指数需要）
            if index_style == "overseas":
                usd_cny = None
                try:
                    df_fx: Any = ak.fx_spot_quote()
                    if df_fx is not None and not df_fx.empty:
                        usd_row = df_fx[df_fx["货币对"] == "美元/人民币"]
                        if not usd_row.empty:
                            usd_cny = float(usd_row.iloc[-1]["最新价"])
                except Exception:
                    logger.debug("外汇即期接口失败，回退中行牌价")
                if usd_cny is None:
                    # 回退：中国银行外汇牌价（央行中间价，单位分 → 元）
                    try:
                        start = (datetime.now().astimezone().date() - timedelta(days=7)).strftime("%Y%m%d")
                        end = datetime.now().astimezone().date().strftime("%Y%m%d")
                        df_boc: Any = ak.currency_boc_sina(symbol="美元", start_date=start, end_date=end)
                        if df_boc is not None and not df_boc.empty:
                            usd_cny = float(df_boc.iloc[-1]["央行中间价"]) / 100.0
                    except Exception:
                        logger.debug("中行外汇牌价接口失败")
                result.usd_cny = usd_cny

        except Exception as e:
            logger.warning(f"获取宏观数据失败: {e}")

        return [result]

    def _fetch_us_index_macro(self, symbol: str) -> list[MacroContext]:
        source = "AkShare 美国 CPI／东方财富国债收益率"
        macro = MacroContext(symbol=symbol, fetch_date=datetime.now().astimezone().date())
        macro._data_source = source
        specs = (
            ("us_cpi_yoy", "美国 CPI", "macro_usa_cpi_yoy", "时间", "现值", "发布日期"),
            ("us_10y_yield", "美国 10 年期国债收益率", "bond_zh_us_rate", "日期", "美国国债收益率10年", ""),
        )
        failures = []
        for key, label, endpoint, day_column, value_column, release_column in specs:
            try:
                frame: Any = getattr(ak, endpoint)()
            except Exception as exc:
                logger.warning("%s 采集失败：%s", label, type(exc).__name__)
                failures.append(f"{label} 请求失败")
                continue
            columns = [day_column, value_column, *([release_column] if release_column else [])]
            if frame is None or any(column not in frame for column in columns):
                failures.append(f"{label} 返回缺少字段")
                continue
            latest = None
            for values in frame[columns].itertuples(index=False, name=None):
                try:
                    day = _parse_date(values[0])
                    value = finite_number(values[1])
                    release = _parse_date(values[2]) if release_column else day
                except (TypeError, ValueError):
                    continue
                if (value is not None and day <= release <= macro.fetch_date
                        and (latest is None or day > latest[0])):
                    latest = day, value, release
            if latest is None:
                failures.append(f"{label} 未取得已发布的有效值")
                continue
            day, value, release = latest
            setattr(macro, key, value)
            macro.indicator_dates[key] = day.isoformat()
            if release_column:
                macro.indicator_dates[f"{key}_release"] = release.isoformat()
        macro.notes.extend(failures)
        if failures:
            self._collection_diagnostics.record = make_record(
                source, "partial" if macro.indicator_dates else "failed",
                error_kind="partial_source" if macro.indicator_dates else "fetch_failed",
                error_message="；".join(failures),
            )
        return [macro] if macro.indicator_dates else []

    @retry_on_network_error()
    def _fetch_index_sentiment(self, symbol: str, market: str = "a-shares", name: str = "") -> list[NewsData]:
        if market in {"hk", "us"}:
            from data.hk_news import SOURCE
            from index.index_news import search_index_news

            try:
                items = search_index_news(symbol, name, market)
            except Exception as exc:
                logger.warning("指数 %s 关键词新闻采集失败：%s", symbol, type(exc).__name__)
                self._record_collection_error(SOURCE, exc)
                return []
            news = NewsData(symbol=symbol, date=datetime.now().astimezone().date(),
                            headlines=[item["title"] for item in items], index_items=items)
            news._data_source = SOURCE
            return [news]
        if market != "a-shares":
            return []

        actual_source = "东方财富市场要闻接口"
        try:
            # 全市场要闻（主源，东方财富），不用个股新闻接口
            try:
                df = ak.stock_news_main_em()  # pyright: ignore[reportAttributeAccessIssue]
            except AttributeError:
                df = None
            if df is None or df.empty:
                # 主源缺失 → 回退财新要闻接口（列为 summary）
                actual_source = "财新要闻接口"
                df: Any = ak.stock_news_main_cx()
            if df is None or df.empty:
                return [NewsData(symbol=symbol, date=datetime.now().astimezone().date(), headlines=[])]

            if "title" in df.columns:
                headlines = df["title"].head(30).tolist()
            elif "summary" in df.columns:
                headlines = df["summary"].head(30).tolist()
            else:
                headlines = []
            from index.news import normalize_news_item
            items = []
            for row in df.head(10).to_dict("records"):
                item = normalize_news_item(row)
                if item["title"]:
                    items.append(item)
            headlines = [item["title"] for item in items]
            result = NewsData(symbol=symbol, date=datetime.now().astimezone().date(), headlines=headlines, index_items=items)
            result._data_source = actual_source
            return [result]
        except Exception as e:
            logger.warning(f"获取指数舆情失败: {e}")
            self._record_collection_error("指数舆情", e)
            return [NewsData(symbol=symbol, date=datetime.now().astimezone().date(), headlines=[])]

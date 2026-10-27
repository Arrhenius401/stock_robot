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
        kind = "timeout" if isinstance(error, TimeoutError) or "Timeout" in type(error).__name__ else "fetch_failed"
        self._collection_diagnostics.record = DataCollectionRecord(
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
        if data_type == "index_price":
            return self._fetch_index_price(symbol, kwargs.get("index_style", "broad")), self.get_collection_error()
        if data_type == "index_valuation":
            return self._fetch_index_valuation(symbol), self.get_collection_error()
        if data_type == "index_capital_flow":
            return self._fetch_capital_flow(symbol, kwargs.get("index_style", "broad")), self.get_collection_error()
        if data_type == "index_macro":
            return self._fetch_index_macro(symbol, kwargs.get("index_style", "broad")), self.get_collection_error()
        if data_type == "index_sentiment":
            return self._fetch_index_sentiment(symbol), self.get_collection_error()
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
        # 回测等场景可显式指定起止日期（YYYYMMDD）；未完整指定时保持默认近一年（days=250）行为
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        today = datetime.now().astimezone().date()
        if start_date is None or end_date is None:
            days = kwargs.get("days", 250)  # 近一年交易日，覆盖完整行情周期
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
        return results

    def _fetch_valuation(self, symbol: str, **kwargs) -> list[ValuationData]:
        """估值：腾讯实时快照（含 PE/PB，独立口径）。失败返回空列表。"""
        if kwargs.get("market") == "hk":
            return self._fetch_hk_valuation(symbol)
        import requests as _req

        tx_symbol = f"sh{symbol}" if symbol.startswith("6") else f"sz{symbol}"
        try:
            resp = _req.get(f"https://qt.gtimg.cn/q={tx_symbol}", timeout=10)
            fields = resp.text.split("~")
            if len(fields) < 47:
                logger.warning(f"腾讯快照字段数异常: {len(fields)}")
                return []
            pe = float(fields[39]) if fields[39] else None
            pb = float(fields[46]) if fields[46] else None
        except Exception as e:
            logger.debug("腾讯快照获取失败，估值数据缺失")
            self._record_collection_error("腾讯估值快照", e)
            return []
        period = None
        try:
            # 腾讯快照第 30 位为行情时间；失败只影响日期元数据，不丢弃估值。
            stamp = fields[30]
            if len(stamp) == 14 and stamp.isdigit():
                candidate = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(
                    tzinfo=timezone(timedelta(hours=8))).date()
                if candidate <= datetime.now(timezone(timedelta(hours=8))).date():
                    period = candidate
        except ValueError:
            logger.debug("腾讯估值快照行情日期无效，保留数值并标注日期未知")
        return [ValuationData(symbol=symbol, date=datetime.now().astimezone().date(), pe_ttm=pe, pb=pb,
                              pe_as_of=period if pe is not None else None, pb_as_of=period if pb is not None else None)]

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
        cash_meta = period_metadata(statement_rows.get("现金流量表", []))
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
            result.append(FinancialData(symbol=symbol, fiscal_quarter=period,
                revenue=row.get(revenue), net_profit=row.get(profit), basic_eps=row.get(eps),
                total_assets=bs.get(total_assets), total_equity=bs.get(total_equity),
                common_equity=bs.get(common_equity), operating_cash_flow=cf.get("003999"),
                gross_margin=margin, gross_margin_basis=GROSS_MARGIN_BASIS if margin is not None else "",
                period_start=start, period_basis="cumulative" if start is not None else "unknown",
                reporting_basis=reporting_basis, report_currency=currency,
                amount_currency_verified=bool(currency), disclosure_currency=disclosure_currency,
                revenue_basis="银行：经营收入总额" if bank_template else "",
                cash_flow_basis="reported" if cf.get("003999") is not None and start is not None and currency
                    and cf_start == start and cf_currency == currency else "unknown",
                cash_flow_period_start=cf_start))
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
                             pe_as_of=pe_as_of, pb_as_of=pb_as_of)
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
        item.pe_history, item.pb_history = pe_curve, pb_curve
        item.history_source = "百度港股估值（近一年）" if curves else ""
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
        from data.schemas import IndexPriceData

        if index_style == "strategy":
            from data.index_mapping import IndexMapping
            from index.strategy_data import StrategyDataProvider

            entry = IndexMapping().lookup(symbol)
            provider = entry.provider if entry and entry.provider else "csi"
            return StrategyDataProvider().fetch_prices(symbol, provider=provider)

        try:
            # 宽基指数使用 stock_zh_index_daily_em（主源，东方财富）
            if index_style == "broad":
                df: Any = ak.stock_zh_index_daily_em(symbol=symbol)
                if df is None or df.empty:
                    # 主源不可用 → 回退腾讯源（参数需 sh/sz 前缀）
                    tx_symbol = f"sz{symbol}" if symbol.startswith("399") else f"sh{symbol}"
                    df: Any = ak.stock_zh_index_daily_tx(symbol=tx_symbol)
            elif index_style == "sector":
                # 行业板块指数使用申万指数接口
                df: Any = ak.index_hist_sw(symbol=symbol)
            elif index_style == "overseas":
                # 海外指数用全球指数接口（参数需中文名称）；东财失效时回退新浪源
                try:
                    df: Any = ak.index_global_hist_em(symbol=_OVERSEAS_NAME_MAP.get(symbol, symbol))
                except Exception:
                    logger.debug(f"东财全球指数接口失败 {symbol}，回退新浪源")
                    df = _fetch_overseas_index_sina(symbol)
                if df is None or df.empty:
                    df = _fetch_overseas_index_sina(symbol)
            else:
                return []

            if df is None or df.empty:
                return []

            results = []
            prev_close: float | None = None
            for _, row in df.iterrows():
                try:
                    date_val = _row_get_any(row, "date", "日期", "trade_date", "交易日期")
                    open_val = _row_get_any(row, "open", "开盘", "开盘价")
                    high_val = _row_get_any(row, "high", "最高", "最高价")
                    low_val = _row_get_any(row, "low", "最低", "最低价")
                    close_val = _row_get_any(row, "close", "收盘", "收盘价")
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
            return results
        except Exception as e:
            logger.warning(f"获取指数 {symbol} 行情失败: {e}")
            return []

    @retry_on_network_error()
    def _fetch_index_valuation(self, symbol: str) -> list[IndexValuationData]:
        from data.schemas import IndexValuationData

        try:
            # 使用 index_value_hist_funddb 获取指数估值历史（主源）
            try:
                df = ak.index_value_hist_funddb(symbol=symbol, indicator="市盈率")  # pyright: ignore[reportAttributeAccessIssue]
            except AttributeError:
                df = None
            if df is None or df.empty:
                # 主源缺失 → 回退中证指数估值接口（列为 市盈率1/股息率1，无市净率）
                df: Any = ak.stock_zh_index_value_csindex(symbol=symbol)
                if df is not None and not df.empty:
                    df = df.rename(columns={"市盈率1": "市盈率", "股息率1": "股息率"})
            if df is None or df.empty:
                return []

            latest = df.iloc[-1]
            return [IndexValuationData(
                symbol=symbol,
                date=_parse_date(str(latest["日期"])),
                pe_ttm=float(latest["市盈率"]) if latest.get("市盈率") else None,
                pb=float(latest.get("市净率", 0)) if latest.get("市净率") else None,
                dividend_yield=float(latest.get("股息率", 0)) if latest.get("股息率") else None,
            )]
        except Exception as e:
            logger.warning(f"获取指数 {symbol} 估值失败: {e}")
            return []

    @retry_on_network_error()
    def _fetch_capital_flow(self, symbol: str, index_style: str) -> list[CapitalFlowData]:
        from data.schemas import CapitalFlowData

        try:
            today = datetime.now().astimezone().date()
            if index_style == "broad":
                # 全市场北向资金（主源）
                try:
                    df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")  # pyright: ignore[reportAttributeAccessIssue]
                except AttributeError:
                    df = None
                if df is None or df.empty:
                    # 主源缺失 → 回退沪深港通资金汇总接口（过滤北向，汇总净流入）
                    summary: Any = ak.stock_hsgt_fund_flow_summary_em()
                    if summary is not None and not summary.empty:
                        north = summary[summary["资金方向"] == "北向"]
                        if not north.empty:
                            return [CapitalFlowData(
                                symbol=symbol, date=today,
                                north_bound=float(north["资金净流入"].sum()),
                            )]
                    return [CapitalFlowData(symbol=symbol, date=today)]
            elif index_style == "sector":
                # 行业板块资金流向
                df: Any = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
                row = df[df["名称"].str.contains(symbol[:3])] if not df.empty else None
                if row is not None and not row.empty:
                    r = row.iloc[0]
                    return [CapitalFlowData(
                        symbol=symbol, date=today,
                        main_net_inflow=float(r.get("主力净流入", 0)) if r.get("主力净流入") else None,
                    )]
                return [CapitalFlowData(symbol=symbol, date=today)]
            else:
                return [CapitalFlowData(symbol=symbol, date=today)]

            if df is not None and not df.empty:
                latest = df.iloc[-1]
                return [CapitalFlowData(
                    symbol=symbol, date=today,
                    north_bound=float(latest.get("value", 0)) if latest.get("value") else None,
                )]
            return [CapitalFlowData(symbol=symbol, date=today)]
        except Exception as e:
            logger.warning(f"获取指数 {symbol} 资金流向失败: {e}")
            return [CapitalFlowData(symbol=symbol, date=datetime.now().astimezone().date())]

    @retry_on_network_error()
    def _fetch_index_macro(self, symbol: str, index_style: str) -> list[MacroContext]:
        from data.schemas import MacroContext

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

            # Shibor（3 个月期；兼容新旧 akshare 参数）
            try:
                df_shibor: Any = ak.rate_interbank(market="上海银行间同业拆放利率", indicator="Shibor")
                three_month = df_shibor[df_shibor["期限"] == "3M"]
                if not three_month.empty:
                    result.shibor_3m = float(three_month.iloc[-1]["利率"])
            except Exception:
                df_shibor: Any = ak.rate_interbank(market="上海银行同业拆借市场", symbol="Shibor人民币", indicator="3月")
                if df_shibor is not None and not df_shibor.empty:
                    result.shibor_3m = float(df_shibor.iloc[-1]["利率"])

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

    @retry_on_network_error()
    def _fetch_index_sentiment(self, symbol: str) -> list[NewsData]:
        from data.schemas import NewsData

        try:
            # 全市场要闻（主源，东方财富），不用个股新闻接口
            try:
                df = ak.stock_news_main_em()  # pyright: ignore[reportAttributeAccessIssue]
            except AttributeError:
                df = None
            if df is None or df.empty:
                # 主源缺失 → 回退财新要闻接口（列为 summary）
                df: Any = ak.stock_news_main_cx()
            if df is None or df.empty:
                return [NewsData(symbol=symbol, date=datetime.now().astimezone().date(), headlines=[])]

            if "title" in df.columns:
                headlines = df["title"].head(30).tolist()
            elif "summary" in df.columns:
                headlines = df["summary"].head(30).tolist()
            else:
                headlines = []
            result = NewsData(symbol=symbol, date=datetime.now().astimezone().date(), headlines=headlines)
            return [result]
        except Exception as e:
            logger.warning(f"获取指数舆情失败: {e}")
            return [NewsData(symbol=symbol, date=datetime.now().astimezone().date(), headlines=[])]

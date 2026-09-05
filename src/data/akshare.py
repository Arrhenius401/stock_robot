"""AkShare 数据源适配器 — A 股数据采集"""
import logging
from datetime import date, datetime, timedelta
from typing import Any, cast

import akshare as ak

# akshare 无完整类型标注，接口返回值形态随版本变化，统一按 Any 处理
ak = cast(Any, ak)

from data.base import DataSource
from data.schemas import (
    CapitalFlowData,
    FinancialData,
    IndexPriceData,
    IndexValuationData,
    IndustryData,
    MacroContext,
    NewsData,
    PriceData,
    ValuationData,
)
from utils.numbers import parse_cn_number
from utils.retry import retry_on_network_error

logger = logging.getLogger(__name__)

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


def get_total_shares(symbol: str, financials: list | None = None) -> float | None:
    """总股本三级链：东财轻量接口 → 腾讯流通股本 → 财报反推（离线兜底）

    财报反推口径：累计净利润 ÷ 累计基本每股收益，禁止单季/累计混用。
    """
    # 1. 东财轻量接口（在线优先）
    try:
        df: Any = _ak_individual_info_em(symbol)
        if df is not None and "item" in df.columns and "value" in df.columns:
            info = dict(zip(df["item"], df["value"]))
            shares = parse_cn_number(str(info.get("总股本", "")))
            if shares is not None and shares > 0:
                return shares
    except Exception:
        logger.debug("东财总股本获取失败，切换腾讯源")
    # 2. 腾讯流通股本（在线）
    try:
        end = datetime.now().astimezone().date().strftime("%Y%m%d")
        start = (datetime.now().astimezone().date() - timedelta(days=10)).strftime("%Y%m%d")
        df: Any = _ak_daily(symbol=symbol, start_date=start, end_date=end, adjust="")
        if df is not None and "outstanding_share" in df.columns and len(df) > 0:
            last = df["outstanding_share"].iloc[-1]
            if last is not None and str(last) not in ("nan", "None") and float(last) > 0:
                return float(last)
    except Exception:
        logger.debug("腾讯流通股本获取失败，切换财报反推")
    # 3. 财报反推（离线兜底）
    if financials:
        fin = sorted(financials, key=lambda x: x.fiscal_quarter)
        latest = fin[-1]
        # >0 判断统一口径，避免 NaN 真值通过
        if latest.net_profit is not None and latest.basic_eps is not None and latest.net_profit > 0 and latest.basic_eps > 0:
            return latest.net_profit / latest.basic_eps
    return None


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
    """将同花顺百分比字段转为小数，负百分比同样按绝对值判断单位。"""
    if value is not None and abs(value) >= 1:
        return value / 100.0
    return value


@retry_on_network_error()
def _ak_hist(**kwargs):
    return ak.stock_zh_a_hist(**kwargs)


@retry_on_network_error()
def _ak_daily(symbol, start_date, end_date, adjust):
    """腾讯源日线数据，symbol 格式: sz000001 / sh600000"""
    if symbol.startswith("6"):
        tx_symbol = f"sh{symbol}"
    else:
        tx_symbol = f"sz{symbol}"
    return ak.stock_zh_a_daily(symbol=tx_symbol, start_date=start_date, end_date=end_date, adjust=adjust)


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

    def supports(self, market: str, data_type: str) -> bool:
        if market == "a-shares" and data_type in (
            "price", "financial", "valuation", "industry", "news"
        ):
            return True
        elif data_type in ("index_price", "index_valuation", "index_capital_flow",
                           "index_macro", "index_sentiment"):
            return market in ("a-shares", "hk", "us")
        return False

    def fetch(self, symbol: str, **kwargs) -> list:
        data_type = kwargs.get("data_type", "price")
        # 指数数据分发（需显式传入 index_style 默认值，故单独处理）
        if data_type == "index_price":
            return self._fetch_index_price(symbol, kwargs.get("index_style", "broad"))
        elif data_type == "index_valuation":
            return self._fetch_index_valuation(symbol)
        elif data_type == "index_capital_flow":
            return self._fetch_capital_flow(symbol, kwargs.get("index_style", "broad"))
        elif data_type == "index_macro":
            return self._fetch_index_macro(symbol, kwargs.get("index_style", "broad"))
        elif data_type == "index_sentiment":
            return self._fetch_index_sentiment(symbol)
        try:
            method = getattr(self, f"_fetch_{data_type}", None)
            if method is None:
                logger.warning(f"AkShare 不支持数据类型: {data_type}")
                return []
            return method(symbol, **kwargs)
        except Exception as e:
            logger.error(f"AkShare 数据获取失败 {symbol}/{data_type}: {e}")
            return []

    def _fetch_price(self, symbol: str, **kwargs) -> list[PriceData]:
        # 回测等场景可显式指定起止日期（YYYYMMDD）；未完整指定时保持默认近一年（days=250）行为
        results: list[PriceData] = []
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        if start_date is None or end_date is None:
            today = datetime.now().astimezone().date()
            days = kwargs.get("days", 250)  # 近一年交易日，覆盖完整行情周期
            start_date = start_date or (today - timedelta(days=days)).strftime("%Y%m%d")
            end_date = end_date or today.strftime("%Y%m%d")

        def _parse(df, source_label: str) -> list[PriceData]:
            results = []
            prev_close: float | None = None
            for _, row in df.iterrows():
                try:
                    date_val = row.get("date", row.get("日期"))
                    open_val = row.get("open", row.get("开盘"))
                    high_val = row.get("high", row.get("最高"))
                    low_val = row.get("low", row.get("最低"))
                    close_val = row.get("close", row.get("收盘"))
                    vol_val = row.get("volume", row.get("成交量"))
                    close_f = float(close_val)
                    # 涨跌幅：优先取源数据列（东方财富），缺失/坏值则按前收盘计算（腾讯源）。
                    # 解析隔离在独立 try 中，坏 pct 值不连累整行 OHLCV 数据。
                    pct_raw = row.get("涨跌幅", row.get("pct_chg"))
                    change_pct = None
                    if pct_raw is not None and str(pct_raw) not in ("", "nan"):
                        try:
                            change_pct = round(float(pct_raw), 2)
                        except (ValueError, TypeError) as e:
                            logger.debug(f"涨跌幅解析失败，回退按前收盘计算: {e}")
                    if change_pct is None and prev_close:
                        change_pct = round((close_f - prev_close) / prev_close * 100, 2)
                    results.append(PriceData(
                        symbol=symbol,
                        trade_date=datetime.strptime(str(date_val)[:10], "%Y-%m-%d").astimezone().date(),
                        open=float(open_val),
                        high=float(high_val),
                        low=float(low_val),
                        close=close_f,
                        volume=int(float(vol_val)),
                        change_pct=change_pct,
                    ))
                    # 异常行不更新 prev_close（真实数据中异常行罕见；若连续异常，
                    # 下一正常行将相对最后正常收盘计算，属可接受的近似）
                    prev_close = close_f
                except (ValueError, KeyError) as e:
                    logger.warning(f"跳过异常行情数据行: {e}")
            return results

        # 优先使用腾讯源
        try:
            df = _ak_daily(symbol=symbol, start_date=start_date, end_date=end_date, adjust="qfq")
            results = _parse(df, "tencent")
            if len(results) >= 60:
                return results
            logger.warning(f"腾讯源仅返回 {len(results)} 条行情数据（不足 60），尝试东方财富源")
        except Exception as e:
            logger.warning(f"腾讯源行情获取失败: {e}")

        # 腾讯源数据不足或失败 → 回退东方财富源
        try:
            df = _ak_hist(symbol=symbol, period="daily",
                          start_date=start_date, end_date=end_date, adjust="qfq")
            results = _parse(df, "eastmoney")
            if len(results) >= 60:
                return results
            logger.warning(f"东方财富源仅返回 {len(results)} 条行情数据（不足 60）")
        except Exception as e:
            logger.warning(f"东方财富源行情获取也失败: {e}")

        # 两个源都不足，返回能拿到的那份（即使不足 60 条）
        return results

    def _fetch_financial(self, symbol: str, **kwargs) -> list[FinancialData]:
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
                    basic_eps=basic_eps,
                ))
            except (ValueError, IndexError, TypeError) as e:
                logger.warning(f"跳过异常财务数据行 {idx}: {e}")
        return results

    def _fetch_valuation(self, symbol: str, **kwargs) -> list[ValuationData]:
        """估值：腾讯实时快照（含 PE/PB，独立口径）。失败返回空列表。"""
        import requests as _req

        tx_symbol = f"sh{symbol}" if symbol.startswith("6") else f"sz{symbol}"
        try:
            resp = _req.get(f"https://qt.gtimg.cn/q={tx_symbol}", timeout=10)
            fields = resp.text.split("~")
            # 字段: [3]=现价, [39]=PE(TTM), [46]=PB；字段数不足视为上游结构变化
            if len(fields) < 47:
                logger.warning(f"腾讯快照字段数异常: {len(fields)}")
                return []
            pe = float(fields[39]) if fields[39] else None
            pb = float(fields[46]) if fields[46] else None
        except Exception:  # 第三方网络边界，兜底降级
            logger.debug("腾讯快照获取失败，估值数据缺失")
            return []

        return [ValuationData(symbol=symbol, date=datetime.now().astimezone().date(),
                              pe_ttm=pe, pb=pb, ps_ttm=None)]

    def _fetch_industry(self, symbol: str, **kwargs) -> list[IndustryData]:
        from data.schemas import PeerBasicInfo

        # 申万二级优先构建同业池；东财行业仅在申万缺失时作展示级降级，绝不参与评分。
        sw_level1 = str(kwargs.get("sw_level1", "")).strip()
        sw_level2 = str(kwargs.get("sw_level2", "")).strip()
        industry = sw_level2 or sw_level1 or _get_industry_name(symbol)
        sector = sw_level1
        top_peers = []
        all_peer_symbols: list[str] = []
        target_mcap: float | None = None
        target_rank: int | None = None
        peer_scope = ""
        peer_industry = ""

        if sw_level1 or sw_level2:
            sw_peers, peer_scope = _fetch_sw_peers(sw_level2, sw_level1)
            if sw_peers:
                if peer_scope == "申万二级":
                    try:
                        from data.industry_mapping_builder import backfill_peer_pool
                        backfill_peer_pool([peer["symbol"] for peer in sw_peers], sw_level1, sw_level2)
                    except Exception as e:
                        logger.warning("申万同行池回填失败 %s/%s: %s", sw_level1, sw_level2, e)
                # 过滤无效市值，按市值排序
                valid_peers = [p for p in sw_peers if p.get("market_cap")]
                valid_peers.sort(key=lambda x: x.get("market_cap", 0), reverse=True)

                # 目标排名使用完整行业池；同行池排除目标自身，避免自比较污染中位数。
                for i, p in enumerate(valid_peers):
                    if p["symbol"] == symbol:
                        target_mcap = p.get("market_cap")
                        target_rank = i + 1
                        break

                comparable_peers = [p for p in valid_peers if p["symbol"] != symbol]
                all_peer_symbols = [p["symbol"] for p in comparable_peers]
                peer_industry = sw_level2 if peer_scope == "申万二级" else sw_level1

                for p in comparable_peers[:5]:
                    top_peers.append(PeerBasicInfo(
                        symbol=p["symbol"], name=p["name"],
                        market_cap=p.get("market_cap"),
                        pe_ttm=p.get("pe_ttm"),
                        pb=p.get("pb"),
                    ))

        result = IndustryData(
            symbol=symbol, industry=industry or "未知", sector=sector or "",
            peers=all_peer_symbols, peer_scope=peer_scope, peer_industry=peer_industry,
            top_peers=top_peers,
        )
        if target_mcap is not None:
            result._target_mcap = target_mcap
        if target_rank is not None:
            result._target_rank = target_rank
        return [result]

    def _fetch_news(self, symbol: str, **kwargs) -> list[NewsData]:
        """拉取近 30 天新闻和公告，附带 RawSentimentData"""
        from data.schemas import RawSentimentData, RawSentimentItem

        today = datetime.now().astimezone().date()
        start_date = today - timedelta(days=30)
        items = []
        seen_titles = set()

        # 拉取新闻
        try:
            df: Any = _ak_news(symbol)
            for _, row in df.head(20).iterrows():
                title = str(row.get("标题", "") or row.get("title", "") or row.get("新闻标题", ""))
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                content = str(row.get("内容", "") or row.get("content", ""))[:500]
                pub_date = today
                try:
                    raw_date = row.get("发布时间", "") or row.get("时间", "")
                    if raw_date:
                        pub_date = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").astimezone().date()
                except (ValueError, TypeError):
                    logger.debug("新闻发布时间解析失败，使用今天日期")
                if pub_date >= start_date:
                    items.append(RawSentimentItem(
                        title=title, source="news", publish_date=pub_date, content=content,
                    ))
        except Exception as e:
            logger.warning(f"新闻数据获取失败: {e}")

        # 拉取公告（个股接口，直接按代码过滤，替代全市场拉取再过滤）
        try:
            notice_df: Any = ak.stock_individual_notice_report(security=symbol)
            if notice_df is not None and not notice_df.empty:
                for _, row in notice_df.head(20).iterrows():
                    title = str(row.get("公告标题", "") or row.get("标题", ""))
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    pub_date = today
                    raw_date = row.get("公告日期", "") or row.get("日期", "")
                    if raw_date:
                        try:
                            pub_date = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").astimezone().date()
                        except (ValueError, TypeError):
                            logger.debug("公告日期解析失败，使用今天日期")
                    if pub_date >= start_date:
                        items.append(RawSentimentItem(
                            title=title, source="announcement",
                            publish_date=pub_date, content="",
                        ))
        except Exception as e:
            logger.warning(f"公告数据获取失败: {e}")

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

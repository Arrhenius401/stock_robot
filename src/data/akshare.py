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

# 单次分析生命周期内复用 stock_individual_info_em 结果
_info_cache: dict[str, dict] = {}

# 海外指数代码 → 全球指数接口所需的中文名称
_OVERSEAS_NAME_MAP = {
    "HSI": "恒生指数",
    "HSCEI": "恒生中国企业指数",
    "SPX": "标普500",
    "IXIC": "纳斯达克综合",
    "DJI": "道琼斯工业平均",
}


def clear_info_cache():
    """清空个股信息缓存（测试用）"""
    _info_cache.clear()


def get_individual_info(symbol: str) -> dict:
    """获取个股基本信息（带内存缓存），返回 {item: value} 字典"""
    if symbol not in _info_cache:
        try:
            if symbol.startswith("6"):
                xq_symbol = f"SH{symbol}"
            else:
                xq_symbol = f"SZ{symbol}"
            df: Any = ak.stock_individual_basic_info_xq(symbol=xq_symbol)
            if "item" in df.columns and "value" in df.columns:
                _info_cache[symbol] = dict(zip(df["item"], df["value"]))
            else:
                _info_cache[symbol] = {}
        except Exception:
            logger.debug(f"获取个股基本信息失败: {symbol}")
            _info_cache[symbol] = {}
    return _info_cache[symbol]


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
def _ak_spot_em():
    return ak.stock_zh_a_spot_em()


@retry_on_network_error()
def _ak_industry_name():
    return ak.stock_board_industry_name_em()


@retry_on_network_error()
def _ak_individual_info_em(symbol):
    """单只股票基本信息接口（轻量，含行业字段）"""
    return ak.stock_individual_info_em(symbol=symbol)


@retry_on_network_error()
def _ak_news(symbol):
    return ak.stock_news_em(symbol=symbol)


@retry_on_network_error()
def _ak_individual_spot_xq(symbol):
    """单只股票行情接口（雪球，轻量，替代全市场扫描）"""
    # 雪球 symbol 格式: SH600000 / SZ000001
    if symbol.startswith("6"):
        xq_symbol = f"SH{symbol}"
    else:
        xq_symbol = f"SZ{symbol}"
    return ak.stock_individual_spot_xq(symbol=xq_symbol)


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


def _fetch_sw_peers(industry_name: str) -> list[dict[str, Any]]:
    """通过申万行业分类获取同行股票（含 PE/PB/市值，来源 legulegu.com）
    返回 list[dict]，每个 dict 包含: symbol, name, market_cap, pe_ttm, pb
    """
    from io import StringIO as _StringIO

    import pandas as _pd
    import requests as _req
    from bs4 import BeautifulSoup as _BeautifulSoup

    # 第一步：获取申万三级行业代码列表（带浏览器请求头，绕过 Cloudflare）
    sw_codes_map: dict[str, list[str]] = {}  # broad_name -> [SW codes]
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://legulegu.com/",
        }
        url = "https://legulegu.com/stockdata/sw-industry-overview"
        resp = _req.get(url, headers=headers, timeout=15)
        soup = _BeautifulSoup(resp.text, "html.parser")
        level3 = soup.find("div", id="level3Items")
        if level3:
            code_items = level3.find_all("div", class_="lg-industries-item-chinese-title")
            name_items = level3.find_all("div", class_="lg-industries-item-number")
            codes = [item.get_text().strip() for item in code_items]
            for code, name_item in zip(codes, name_items):
                full_text = name_item.get_text()
                parent_name = ""
                # 提取上级行业名称（格式: "行业名(成分数)" 或 "行业名（成分数）"）
                span = name_item.find("span")
                if span:
                    parent_name = span.get_text().strip(" ()（）")
                    # 去掉最后的 ( ) 中内容
                    if "(" in parent_name:
                        parent_name = parent_name.rsplit("(", 1)[0].strip()
                    if "（" in parent_name:
                        parent_name = parent_name.rsplit("（", 1)[0].strip()
                # 尝试从上级行业名匹配；也尝试从行业名称匹配
                # 行业名称格式: 行业名Ⅲ(成分数)，取行业名部分
                industry_detail = full_text.split("(")[0].split("（")[0].strip()
                # 去掉 Ⅲ、Ⅱ、Ⅰ 后缀
                broad = industry_detail.rstrip("ⅢⅡⅠ")
                if broad not in sw_codes_map:
                    sw_codes_map[broad] = []
                sw_codes_map[broad].append(code)
    except Exception:
        logger.warning("无法获取申万行业列表，将使用回退方案")

    # 在映射表中模糊匹配
    matched_codes = []
    for broad, codes in sw_codes_map.items():
        if industry_name in broad or broad in industry_name:
            matched_codes.extend(codes)

    # 去重
    matched_codes = list(set(matched_codes))

    if not matched_codes:
        logger.info(f"未找到与 '{industry_name}' 匹配的申万行业")
        return []

    # 第二步：从 legulegu.com 直接抓取成分股数据
    peers: list[dict[str, Any]] = []
    for sw_code in matched_codes:
        try:
            url = f"https://legulegu.com/stockdata/index-composition?industryCode={sw_code}"
            resp = _req.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            dfs = _pd.read_html(_StringIO(resp.text))
            if not dfs:
                continue
            df = dfs[0]

            # 清理列名（去掉网站注入的 JSON-LD schema.org 标记）
            clean_cols = {}
            for col in df.columns:
                col_str = str(col)
                if "  " in col_str:
                    clean_cols[col] = col_str.split("  ")[0].strip()
            df.rename(columns=clean_cols, inplace=True)

            for _, row in df.iterrows():
                try:
                    code = str(row.get("股票代码", ""))
                    name = str(row.get("股票简称", ""))
                    if not code or any(tag in name for tag in ("ST", "退市", "PT")):
                        continue
                    # 股票代码格式: 601398.SH → 去掉后缀
                    if "." in code:
                        code = code.split(".")[0]

                    mcap_raw: Any = row.get("市值（亿元）")
                    pe_raw: Any = row.get("市盈率ttm")
                    pb_raw: Any = row.get("市净率")

                    peers.append({
                        "symbol": code,
                        "name": name,
                        "market_cap": float(mcap_raw) * 1e8 if mcap_raw is not None and str(mcap_raw) not in ("nan", "") else None,
                        "pe_ttm": float(pe_raw) if pe_raw is not None and str(pe_raw) not in ("nan", "") else None,
                        "pb": float(pb_raw) if pb_raw is not None and str(pb_raw) not in ("nan", "") else None,
                    })
                except (ValueError, TypeError):
                    continue
        except Exception:
            logger.warning(f"获取申万行业成分股失败: {sw_code}")
            continue

    return peers


def _parse_debt_new(bs_df: Any) -> dict[str, tuple[float | None, float | None]]:
    """解析 THS 新长表资产负债表 → {报告期: (equity, assets)}"""
    balance_map: dict[str, tuple[float | None, float | None]] = {}
    per_date: dict[str, dict[str, float | None]] = {}
    for _, row in bs_df.iterrows():
        period = str(row.get("report_date", ""))
        try:
            period_date = datetime.strptime(period, "%Y-%m-%d").astimezone().date().isoformat()
        except ValueError:
            continue
        name = str(row.get("metric_name", ""))
        if name in ("assets_total", "holder_equity_total", "debt_and_equity_total"):
            per_date.setdefault(period_date, {})[name] = parse_cn_number(row.get("value"))
    for period_date, vals in per_date.items():
        assets = vals.get("assets_total")
        if assets is None:
            assets = vals.get("debt_and_equity_total")
        balance_map[period_date] = (vals.get("holder_equity_total"), assets)
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
        days = kwargs.get("days", 250)  # 近一年交易日，覆盖完整行情周期
        end_date = datetime.now().astimezone().date().strftime("%Y%m%d")
        start_date = (datetime.now().astimezone().date() - timedelta(days=days)).strftime("%Y%m%d")

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
        balance_map: dict[str, tuple[float | None, float | None]] = {}
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
                balance_map[period_date] = (equity, assets)
        except Exception:
            logger.debug("旧版资产负债表接口失败，尝试新版长表接口")
            try:
                balance_map = _parse_debt_new(ak.stock_financial_debt_new_ths(symbol=symbol))
            except Exception:
                logger.debug("资产负债表数据获取失败，将使用利润表数据")

        results = []
        periods = df.get("报告期", [])
        revenues = df.get("营业总收入", [])
        profits = df.get("净利润", [])
        deducted_profits = df.get("扣非净利润", [])
        roe_list = df.get("净资产收益率", [])  # ROE（%）
        net_margins = df.get("销售净利率", [])  # 销售净利率（%）
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

                # 净资产收益率 — 源数据为百分比（如 12.5），> 1 时除以 100 转为小数
                roe_raw = parse_cn_number(roe_list.iloc[idx] if hasattr(roe_list, 'iloc') else roe_list[idx]) if idx < len(roe_list) else None
                roe = roe_raw / 100.0 if roe_raw is not None and roe_raw > 1 else roe_raw

                # 销售净利率 — 源数据为百分比（如 12.5），> 1 时除以 100 转为小数
                nm_raw = parse_cn_number(net_margins.iloc[idx] if hasattr(net_margins, 'iloc') else net_margins[idx]) if idx < len(net_margins) else None
                net_margin = nm_raw / 100.0 if nm_raw is not None and nm_raw > 1 else nm_raw

                # 每股经营现金流 × 总股本 → 经营现金流总额
                ocf_per_share = parse_cn_number(cash_flow_per_share.iloc[idx] if hasattr(cash_flow_per_share, 'iloc') else cash_flow_per_share[idx]) if idx < len(cash_flow_per_share) else None
                basic_eps = parse_cn_number(basic_eps_list.iloc[idx] if hasattr(basic_eps_list, 'iloc') else basic_eps_list[idx]) if idx < len(basic_eps_list) else None
                ocf = None
                if ocf_per_share is not None and net_profit is not None and basic_eps is not None and basic_eps > 0:
                    total_shares = net_profit / basic_eps
                    ocf = ocf_per_share * total_shares
                elif ocf_per_share is not None:
                    ocf = ocf_per_share  # 降级：无法反推总股本时保留 per-share 值

                # 从资产负债表映射中获取净资产和总资产
                date_key = fiscal_date.isoformat()
                total_equity, total_assets = balance_map.get(date_key, (None, None))

                results.append(FinancialData(
                    symbol=symbol,
                    fiscal_quarter=fiscal_date,
                    revenue=revenue,
                    net_profit=net_profit,
                    deducted_net_profit=deducted,
                    total_assets=total_assets,
                    total_equity=total_equity,
                    operating_cash_flow=ocf,
                    roe=roe,
                    gross_margin=net_margin,
                    basic_eps=basic_eps,
                ))
            except (ValueError, IndexError, TypeError) as e:
                logger.warning(f"跳过异常财务数据行 {idx}: {e}")
        return results

    def _fetch_valuation(self, symbol: str, **kwargs) -> list[ValuationData]:
        pe_ttm, pb = None, None

        # 优先：单只股票轻量接口（雪球）
        try:
            df: Any = _ak_individual_spot_xq(symbol)
            if "item" in df.columns and "value" in df.columns:
                pe_row = df[df["item"] == "市盈率(动)"]
                pb_row = df[df["item"] == "市净率"]
                if not pe_row.empty:
                    pe_ttm = parse_cn_number(pe_row["value"].iloc[0])
                if not pb_row.empty:
                    pb = parse_cn_number(pb_row["value"].iloc[0])
        except Exception:
            logger.debug("雪球估值接口失败，回退全市场接口")

        # 回退：旧全市场接口
        if pe_ttm is None and pb is None:
            try:
                df: Any = _ak_spot_em()
                row = df[df["代码"] == symbol]
                pe_ttm = parse_cn_number(row["市盈率-动态"].iloc[0]) if not row.empty and row["市盈率-动态"].iloc[0] != "-" else None
                pb = parse_cn_number(row["市净率"].iloc[0]) if not row.empty and row["市净率"].iloc[0] != "-" else None
            except Exception:
                logger.debug("全市场估值接口失败，估值字段为空")

        return [ValuationData(symbol=symbol, date=datetime.now().astimezone().date(), pe_ttm=pe_ttm, pb=pb, ps_ttm=None)]

    def _fetch_industry(self, symbol: str, **kwargs) -> list[IndustryData]:
        from data.schemas import PeerBasicInfo

        industry = ""
        sector = ""
        top_peers = []

        # 获取行业分类（带缓存，后续充实层可复用）
        info = get_individual_info(symbol)
        # 雪球源：affiliate_industry 为 {"ind_code": "BK0055", "ind_name": "银行"} 格式
        aff_ind = info.get("affiliate_industry", "")
        if isinstance(aff_ind, dict):
            industry = str(aff_ind.get("ind_name", ""))
        else:
            industry = str(aff_ind) if aff_ind else ""
        sector = str(info.get("classi_name", "") or info.get("板块", "") or info.get("所属部门", ""))

        # 回退：新端点失败时尝试旧行业名称端点
        if not industry or industry == "未知":
            try:
                name_df: Any = _ak_industry_name()
                if "板块名称" in name_df.columns:
                    names = name_df["板块名称"].tolist()
                    if names:
                        industry = str(names[0])
            except Exception:
                logger.debug("行业名称接口失败，使用空行业名")

        # 通过申万行业分类获取同行成分股（优先；东方财富端点不稳定）
        all_peer_symbols: list[str] = []
        target_mcap: float | None = None
        target_rank: int | None = None

        if industry and industry != "未知":
            sw_peers = _fetch_sw_peers(industry)
            if sw_peers:
                # 过滤无效市值，按市值排序
                valid_peers = [p for p in sw_peers if p.get("market_cap")]
                valid_peers.sort(key=lambda x: x.get("market_cap", 0), reverse=True)

                all_peer_symbols = [p["symbol"] for p in valid_peers]

                # 查找目标排名
                for i, p in enumerate(valid_peers):
                    if p["symbol"] == symbol:
                        target_mcap = p.get("market_cap")
                        target_rank = i + 1
                        break

                for p in valid_peers[:5]:
                    top_peers.append(PeerBasicInfo(
                        symbol=p["symbol"], name=p["name"],
                        market_cap=p.get("market_cap"),
                        pe_ttm=p.get("pe_ttm"),
                        pb=p.get("pb"),
                    ))
            else:
                # 回退：东方财富端点
                try:
                    board_df: Any = _ak_board_industry_cons_em(industry)
                    if board_df is not None and len(board_df) > 0:
                        # ...（保留旧逻辑作为回退）
                        cols = list(board_df.columns)
                        code_col = "代码" if "代码" in cols else cols[0]
                        name_col = "名称" if "名称" in cols else (cols[1] if len(cols) > 1 else code_col)
                        mcap_col = None
                        for c in cols:
                            if "市值" in str(c) or "总市值" in str(c):
                                mcap_col = c
                                break
                        valid_rows = []
                        for _, row in board_df.iterrows():
                            code = str(row[code_col])
                            name = str(row.get(name_col, ""))
                            if any(tag in name for tag in ("ST", "退市", "PT")):
                                continue
                            mcap = None
                            if mcap_col:
                                try:
                                    mcap = float(row[mcap_col])
                                except (ValueError, TypeError):
                                    pass
                            if mcap is not None and mcap > 0:
                                valid_rows.append((code, name, mcap))
                        all_peer_symbols = [code for code, _, _ in valid_rows]
                        valid_rows.sort(key=lambda x: x[2], reverse=True)
                        for i, (code, name, mcap) in enumerate(valid_rows):
                            if code == symbol:
                                target_mcap = mcap
                                target_rank = i + 1
                                break
                        for code, name, mcap in valid_rows[:5]:
                            top_peers.append(PeerBasicInfo(
                                symbol=code, name=name, market_cap=mcap,
                            ))
                except Exception:
                    logger.warning(f"获取行业成分股失败: {industry}")

        result = IndustryData(
            symbol=symbol, industry=industry or "未知", sector=sector or "",
            peers=all_peer_symbols, top_peers=top_peers,
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

        # 拉取公告（stock_notice_report 的 symbol 参数是报告类型而非股票代码）
        try:
            today_str = today.strftime("%Y%m%d")
            announce_df: Any = ak.stock_notice_report(symbol="全部", date=today_str)
            if announce_df is not None and not announce_df.empty:
                cols = list(announce_df.columns)
                # 探测列名映射
                title_col = None
                code_col = None
                date_col = None
                for c in cols:
                    c_str = str(c)
                    if "标题" in c_str or "title" in c_str.lower():
                        title_col = c
                    elif "代码" in c_str or "code" in c_str.lower() or "symbol" in c_str.lower():
                        code_col = c
                    elif "日期" in c_str or "date" in c_str.lower():
                        date_col = c
                if title_col is None:
                    title_col = cols[0]

                for _, row in announce_df.head(30).iterrows():
                    # 按股票代码过滤
                    if code_col:
                        cell_code = str(row.get(code_col, ""))
                        if symbol not in cell_code:
                            continue

                    title = str(row.get(title_col, ""))
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    pub_date = today
                    if date_col:
                        try:
                            pub_date = datetime.strptime(str(row[date_col])[:10], "%Y-%m-%d").astimezone().date()
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
                # 海外指数用全球指数接口（参数需中文名称）
                df: Any = ak.index_global_hist_em(symbol=_OVERSEAS_NAME_MAP.get(symbol, symbol))
            else:
                return []

            if df is None or df.empty:
                return []

            results = []
            prev_close: float | None = None
            for _, row in df.iterrows():
                try:
                    close_f = float(row["close"])
                    # 涨跌幅：优先取源数据列（部分源/旧版 akshare 提供），缺失/坏值按前收盘计算。
                    # 注：安装版 akshare 的 stock_zh_index_daily_em 在返回前丢弃承载涨跌幅
                    # 的 "_" 列，腾讯源亦无涨跌幅列，故实际生效的是按前收盘计算；
                    # "_" 回退仅为防御未来版本保留该列的情况。
                    # 解析隔离在独立 try 中，坏 pct 值不连累整行 OHLCV 数据。
                    pct_raw = row.get("涨跌幅", row.get("pct_chg", row.get("_")))
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
                        trade_date=_parse_date(row["date"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=close_f,
                        volume=int(row.get("volume", 0)),
                        turnover=float(row.get("amount", 0)) / 1e8 if row.get("amount") else None,
                        change_pct=change_pct,
                    ))
                    prev_close = close_f
                except (ValueError, KeyError) as e:
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

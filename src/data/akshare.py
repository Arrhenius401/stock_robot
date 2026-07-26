"""AkShare 数据源适配器 — A 股数据采集"""
from datetime import date, datetime, timedelta
import logging
import akshare as ak
from data.base import DataSource
from data.schemas import PriceData, FinancialData, ValuationData, IndustryData, NewsData
from utils.numbers import parse_cn_number
from utils.retry import retry_on_network_error

logger = logging.getLogger(__name__)


@retry_on_network_error()
def _ak_hist(**kwargs):
    return ak.stock_zh_a_hist(**kwargs)


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


class AkShareAdapter(DataSource):
    """AkShare 数据源适配器 — 支持 A 股全部数据类型"""

    def supports(self, market: str, data_type: str) -> bool:
        return market == "a-shares" and data_type in (
            "price", "financial", "valuation", "industry", "news"
        )

    def fetch(self, symbol: str, **kwargs) -> list:
        data_type = kwargs.get("data_type", "price")
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
        end_date = date.today().strftime("%Y%m%d")
        start_date = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
        df = _ak_hist(
            symbol=symbol, period="daily",
            start_date=start_date, end_date=end_date, adjust="qfq"
        )
        results = []
        for _, row in df.iterrows():
            try:
                results.append(PriceData(
                    symbol=symbol,
                    trade_date=datetime.strptime(str(row["日期"]), "%Y-%m-%d").date(),
                    open=float(row["开盘"]),
                    high=float(row["最高"]),
                    low=float(row["最低"]),
                    close=float(row["收盘"]),
                    volume=int(row["成交量"]),
                ))
            except (ValueError, KeyError) as e:
                logger.warning(f"跳过异常行情数据行: {e}")
        return results

    def _fetch_financial(self, symbol: str, **kwargs) -> list[FinancialData]:
        df = ak.stock_financial_abstract_ths(symbol=symbol)
        results = []
        periods = df.get("报告期", [])
        revenues = df.get("营业总收入", [])
        profits = df.get("净利润", [])
        deducted_profits = df.get("扣非净利润", [])
        roe_list = df.get("净资产收益率", [])  # ROE（%）
        net_margins = df.get("销售净利率", [])  # 销售净利率（%）
        cash_flow_per_share = df.get("每股经营现金流", [])

        # 取最近 12 期数据（数据按时间升序排列，最新在末尾）
        total = len(periods)
        start = max(0, total - 12)
        for idx in range(start, total):
            try:
                period_str = str(periods.iloc[idx] if hasattr(periods, 'iloc') else periods[idx])
                try:
                    fiscal_date = datetime.strptime(period_str, "%Y-%m-%d").date()
                except ValueError:
                    fiscal_date = datetime.strptime(period_str, "%Y%m%d").date()

                revenue = parse_cn_number(revenues.iloc[idx] if hasattr(revenues, 'iloc') else revenues[idx]) if idx < len(revenues) else None
                net_profit = parse_cn_number(profits.iloc[idx] if hasattr(profits, 'iloc') else profits[idx]) if idx < len(profits) else None
                deducted = parse_cn_number(deducted_profits.iloc[idx] if hasattr(deducted_profits, 'iloc') else deducted_profits[idx]) if idx < len(deducted_profits) else None

                # 净资产收益率 — 源数据为百分比（如 12.5），> 1 时除以 100 转为小数
                roe_raw = parse_cn_number(roe_list.iloc[idx] if hasattr(roe_list, 'iloc') else roe_list[idx]) if idx < len(roe_list) else None
                roe = roe_raw / 100.0 if roe_raw is not None and roe_raw > 1 else roe_raw

                # 销售净利率 — 源数据为百分比（如 12.5），> 1 时除以 100 转为小数
                nm_raw = parse_cn_number(net_margins.iloc[idx] if hasattr(net_margins, 'iloc') else net_margins[idx]) if idx < len(net_margins) else None
                net_margin = nm_raw / 100.0 if nm_raw is not None and nm_raw > 1 else nm_raw

                # 每股经营现金流 — 总股本未知，暂存 per-share 值
                ocf = parse_cn_number(cash_flow_per_share.iloc[idx] if hasattr(cash_flow_per_share, 'iloc') else cash_flow_per_share[idx]) if idx < len(cash_flow_per_share) else None

                results.append(FinancialData(
                    symbol=symbol,
                    fiscal_quarter=fiscal_date,
                    revenue=revenue,
                    net_profit=net_profit,
                    deducted_net_profit=deducted,
                    total_assets=None,  # 此 API 不提供资产总计
                    total_equity=None,  # 此 API 不提供股东权益
                    operating_cash_flow=ocf,  # 每股经营现金流（非总额）
                    roe=roe,
                    gross_margin=net_margin,  # 此 API 提供的是销售净利率，复用此字段
                ))
            except (ValueError, IndexError, TypeError) as e:
                logger.warning(f"跳过异常财务数据行 {idx}: {e}")
        return results

    def _fetch_valuation(self, symbol: str, **kwargs) -> list[ValuationData]:
        pe_ttm, pb = None, None

        # 优先：单只股票轻量接口（雪球）
        try:
            df = _ak_individual_spot_xq(symbol)
            if "item" in df.columns and "value" in df.columns:
                pe_row = df[df["item"] == "市盈率(动)"]
                pb_row = df[df["item"] == "市净率"]
                if not pe_row.empty:
                    pe_ttm = parse_cn_number(pe_row["value"].iloc[0])
                if not pb_row.empty:
                    pb = parse_cn_number(pb_row["value"].iloc[0])
        except Exception:
            pass

        # 回退：旧全市场接口
        if pe_ttm is None and pb is None:
            try:
                df = _ak_spot_em()
                row = df[df["代码"] == symbol]
                pe_ttm = parse_cn_number(row["市盈率-动态"].iloc[0]) if not row.empty and row["市盈率-动态"].iloc[0] != "-" else None
                pb = parse_cn_number(row["市净率"].iloc[0]) if not row.empty and row["市净率"].iloc[0] != "-" else None
            except Exception:
                pass

        return [ValuationData(symbol=symbol, date=date.today(), pe_ttm=pe_ttm, pb=pb, ps_ttm=None)]

    def _fetch_industry(self, symbol: str, **kwargs) -> list[IndustryData]:
        from data.schemas import PeerBasicInfo

        industry = ""
        sector = ""
        top_peers = []

        # 获取行业分类
        try:
            df = _ak_individual_info_em(symbol)
            if "item" in df.columns and "value" in df.columns:
                ind_row = df[df["item"].str.contains("行业", na=False)]
                if not ind_row.empty:
                    industry = str(ind_row["value"].iloc[0])
                sec_row = df[df["item"].str.contains("板块|部门", na=False)]
                if not sec_row.empty:
                    sector = str(sec_row["value"].iloc[0])
        except Exception:
            pass

        # 从行业板块接口拉取成分股
        all_peer_symbols: list[str] = []
        target_mcap: float | None = None
        target_rank: int | None = None

        if industry and industry != "未知":
            try:
                board_df = _ak_board_industry_cons_em(industry)
                if board_df is not None and len(board_df) > 0:
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
                        # 过滤 ST、*ST、退市标记
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

                    # 记录全部同行符号
                    all_peer_symbols = [code for code, _, _ in valid_rows]

                    # 按市值排序取前 5，同时记录目标股票的排名
                    valid_rows.sort(key=lambda x: x[2], reverse=True)
                    for i, (code, name, mcap) in enumerate(valid_rows):
                        if code == symbol:
                            target_mcap = mcap
                            target_rank = i + 1  # 1-based
                            break

                    for code, name, mcap in valid_rows[:5]:
                        pe_ttm = None
                        pb = None
                        # 容错：单家接口失败不中断
                        try:
                            if code.startswith("6"):
                                xq = f"SH{code}"
                            else:
                                xq = f"SZ{code}"
                            spot_df = _ak_individual_spot_xq(xq)
                            if spot_df is not None and "item" in spot_df.columns and "value" in spot_df.columns:
                                pe_row = spot_df[spot_df["item"] == "市盈率(动)"]
                                pb_row = spot_df[spot_df["item"] == "市净率"]
                                if not pe_row.empty:
                                    pe_ttm = parse_cn_number(pe_row["value"].iloc[0])
                                if not pb_row.empty:
                                    pb = parse_cn_number(pb_row["value"].iloc[0])
                        except Exception:
                            pass
                        top_peers.append(PeerBasicInfo(
                            symbol=code, name=name, market_cap=mcap,
                            pe_ttm=pe_ttm, pb=pb,
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

        today = date.today()
        start_date = today - timedelta(days=30)
        items = []
        seen_titles = set()

        # 拉取新闻
        try:
            df = _ak_news(symbol)
            for _, row in df.head(20).iterrows():
                title = str(row.get("标题", "") or row.get("title", ""))
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                content = str(row.get("内容", "") or row.get("content", ""))[:500]
                pub_date = today
                try:
                    raw_date = row.get("发布时间", "") or row.get("时间", "")
                    if raw_date:
                        pub_date = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
                except Exception:
                    pass
                if pub_date >= start_date:
                    items.append(RawSentimentItem(
                        title=title, source="news", publish_date=pub_date, content=content,
                    ))
        except Exception as e:
            logger.warning(f"新闻数据获取失败: {e}")

        # 拉取公告（stock_notice_report 的 symbol 参数是报告类型而非股票代码）
        try:
            today_str = today.strftime("%Y%m%d")
            announce_df = ak.stock_notice_report(symbol="全部", date=today_str)
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
                            pub_date = datetime.strptime(str(row[date_col])[:10], "%Y-%m-%d").date()
                        except Exception:
                            pass
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

"""AkShare 数据源适配器 — A 股数据采集"""
from datetime import date, datetime, timedelta
import logging
import akshare as ak
from data.base import DataSource
from data.schemas import PriceData, FinancialData, ValuationData, IndustryData, NewsData
from utils.numbers import parse_cn_number
from utils.retry import retry_on_network_error

logger = logging.getLogger(__name__)

# 单次分析生命周期内复用 stock_individual_info_em 结果
_info_cache: dict[str, dict] = {}


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
            df = ak.stock_individual_basic_info_xq(symbol=xq_symbol)
            if "item" in df.columns and "value" in df.columns:
                _info_cache[symbol] = dict(zip(df["item"], df["value"]))
            else:
                _info_cache[symbol] = {}
        except Exception:
            _info_cache[symbol] = {}
    return _info_cache[symbol]


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


def _fetch_sw_peers(industry_name: str) -> list[dict]:
    """通过申万行业分类获取同行股票（含 PE/PB/市值，来源 legulegu.com）
    返回 list[dict]，每个 dict 包含: symbol, name, market_cap, pe_ttm, pb
    """
    import requests as _req
    from io import StringIO as _StringIO
    import pandas as _pd
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
    peers = []
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

                    mcap_raw = row.get("市值（亿元）")
                    pe_raw = row.get("市盈率ttm")
                    pb_raw = row.get("市净率")

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

        def _parse(df, source_label: str) -> list[PriceData]:
            results = []
            for _, row in df.iterrows():
                try:
                    date_val = row.get("date", row.get("日期"))
                    open_val = row.get("open", row.get("开盘"))
                    high_val = row.get("high", row.get("最高"))
                    low_val = row.get("low", row.get("最低"))
                    close_val = row.get("close", row.get("收盘"))
                    vol_val = row.get("volume", row.get("成交量"))
                    results.append(PriceData(
                        symbol=symbol,
                        trade_date=datetime.strptime(str(date_val)[:10], "%Y-%m-%d").date(),
                        open=float(open_val),
                        high=float(high_val),
                        low=float(low_val),
                        close=float(close_val),
                        volume=int(float(vol_val)),
                    ))
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
        df = ak.stock_financial_abstract_ths(symbol=symbol)

        # 从资产负债表端点补充 total_equity / total_assets（同花顺源，非东方财富）
        balance_map: dict[str, tuple[float | None, float | None]] = {}
        try:
            bs_df = ak.stock_financial_debt_ths(symbol=symbol)
            for _, row in bs_df.iterrows():
                period_str = str(row.get("报告期", ""))
                try:
                    period_date = datetime.strptime(period_str, "%Y-%m-%d").date().isoformat()
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
            logger.debug(f"资产负债表数据获取失败，将使用利润表数据")

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
                name_df = _ak_industry_name()
                if "板块名称" in name_df.columns:
                    names = name_df["板块名称"].tolist()
                    if names:
                        industry = str(names[0])
            except Exception:
                pass

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
                    board_df = _ak_board_industry_cons_em(industry)
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

        today = date.today()
        start_date = today - timedelta(days=30)
        items = []
        seen_titles = set()

        # 拉取新闻
        try:
            df = _ak_news(symbol)
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

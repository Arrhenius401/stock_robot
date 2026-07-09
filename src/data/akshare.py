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
def _ak_news(symbol):
    return ak.stock_news_em(symbol=symbol)


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
        days = kwargs.get("days", 365)
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
        assets = df.get("资产总计", [])
        equities = df.get("股东权益合计", [])
        cash_flows = df.get("经营活动现金流量净额", [])

        for i in range(min(len(periods), 12)):
            try:
                period_str = str(periods[i])
                try:
                    fiscal_date = datetime.strptime(period_str, "%Y-%m-%d").date()
                except ValueError:
                    fiscal_date = datetime.strptime(period_str, "%Y%m%d").date()

                equity = parse_cn_number(equities[i]) if i < len(equities) else None
                net_profit = parse_cn_number(profits[i]) if i < len(profits) else None
                revenue = parse_cn_number(revenues[i]) if i < len(revenues) else None

                roe = (net_profit / equity) if (
                    net_profit is not None and equity is not None and equity > 0
                ) else None

                results.append(FinancialData(
                    symbol=symbol,
                    fiscal_quarter=fiscal_date,
                    revenue=revenue,
                    net_profit=net_profit,
                    total_assets=parse_cn_number(assets[i]) if i < len(assets) else None,
                    total_equity=equity,
                    operating_cash_flow=parse_cn_number(cash_flows[i]) if i < len(cash_flows) else None,
                    roe=roe,
                    gross_margin=None,
                ))
            except (ValueError, IndexError, TypeError) as e:
                logger.warning(f"跳过异常财务数据行 {i}: {e}")
        return results

    def _fetch_valuation(self, symbol: str, **kwargs) -> list[ValuationData]:
        try:
            df = _ak_spot_em()
            row = df[df["代码"] == symbol]
            pe_ttm = float(row["市盈率-动态"].iloc[0]) if not row.empty and row["市盈率-动态"].iloc[0] != "-" else None
            pb = float(row["市净率"].iloc[0]) if not row.empty and row["市净率"].iloc[0] != "-" else None
        except Exception:
            pe_ttm, pb = None, None

        return [ValuationData(symbol=symbol, date=date.today(), pe_ttm=pe_ttm, pb=pb, ps_ttm=None)]

    def _fetch_industry(self, symbol: str, **kwargs) -> list[IndustryData]:
        try:
            df = _ak_industry_name()
            industry = ""
            for _, row in df.iterrows():
                industry = str(row.get("板块名称", ""))
                break
            return [IndustryData(symbol=symbol, industry=industry or "未知", sector="", peers=[])]
        except Exception as e:
            logger.warning(f"行业数据获取失败: {e}")
            return [IndustryData(symbol=symbol, industry="未知", sector="", peers=[])]

    def _fetch_news(self, symbol: str, **kwargs) -> list[NewsData]:
        try:
            df = _ak_news(symbol)
            headlines = []
            for _, row in df.head(10).iterrows():
                title = str(row.get("标题", "") or row.get("title", ""))
                if title:
                    headlines.append(title)
            return [NewsData(symbol=symbol, date=date.today(), headlines=headlines)]
        except Exception as e:
            logger.warning(f"新闻数据获取失败: {e}")
            return [NewsData(symbol=symbol, date=date.today(), headlines=[])]

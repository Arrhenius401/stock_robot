"""IndexDataCollector — 按 index_style 编排指数数据采集，只拉取原始数据"""
import logging
from core.registry import Registry
from data.schemas import AnalysisTarget, IndexAnalysisContext
from data.akshare import AkShareAdapter

logger = logging.getLogger(__name__)


class IndexDataCollector:
    """按 index_style 编排采集策略，只拉取原始数据，不做衍生计算"""

    def __init__(self, registry: Registry | None = None):
        self._adapter = AkShareAdapter()
        if registry is not None:
            self._registry = registry
        else:
            self._registry = Registry()
            self._registry.register_data_source(self._adapter)

    def collect(self, target: AnalysisTarget) -> IndexAnalysisContext:
        ctx = IndexAnalysisContext(target=target)

        # 所有类别都采集行情
        price_result = self._adapter.fetch(
            target.symbol, data_type="index_price",
            index_style=target.index_style
        )
        if price_result:
            ctx.price_data = price_result

        # 所有类别都采集估值
        val_result = self._adapter.fetch(
            target.symbol, data_type="index_valuation"
        )
        if val_result:
            ctx.valuation_data = val_result[0]

        # 按类别采集资金流向
        if target.index_style in ("broad", "sector"):
            cf_result = self._adapter.fetch(
                target.symbol, data_type="index_capital_flow",
                index_style=target.index_style
            )
            if cf_result:
                ctx.capital_flow = cf_result[0]

        # 按类别采集宏观数据
        if target.index_style in ("broad", "overseas"):
            macro_result = self._adapter.fetch(
                target.symbol, data_type="index_macro",
                index_style=target.index_style
            )
            if macro_result:
                ctx.macro = macro_result[0]
        elif target.index_style == "sector":
            from data.schemas import MacroContext
            from datetime import date
            ctx.macro = MacroContext(symbol=target.symbol, fetch_date=date.today())

        # 舆情
        sentiment_result = self._adapter.fetch(
            target.symbol, data_type="index_sentiment"
        )
        if sentiment_result and len(sentiment_result) > 0:
            from data.schemas import RawSentimentData, RawSentimentItem
            news = sentiment_result[0]
            ctx.raw_sentiment = RawSentimentData(
                symbol=target.symbol,
                fetch_date=news.date,
                items=[RawSentimentItem(
                    title=h, source="market_news",
                    publish_date=news.date
                ) for h in (news.headlines or [])]
            )

        return ctx

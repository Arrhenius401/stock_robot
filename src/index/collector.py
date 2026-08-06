"""IndexDataCollector — 按 index_style 编排指数数据采集，只拉取原始数据"""
import logging
from collections.abc import Callable
from core.registry import Registry
from core.pipeline import ProgressCallback
from data.schemas import AnalysisTarget, IndexAnalysisContext
from data.akshare import AkShareAdapter

logger = logging.getLogger(__name__)

INDEX_COLLECT_LABELS = {
    "index_price": "采集行情数据",
    "index_valuation": "采集估值数据",
    "index_capital_flow": "采集资金流向数据",
    "index_macro": "采集宏观数据",
    "index_sentiment": "采集舆情数据",
}


class IndexDataCollector:
    """按 index_style 编排采集策略，只拉取原始数据，不做衍生计算"""

    def __init__(self, registry: Registry | None = None):
        self._adapter = AkShareAdapter()
        if registry is not None:
            self._registry = registry
        else:
            self._registry = Registry()
            self._registry.register_data_source(self._adapter)

    def collect(self, target: AnalysisTarget, on_progress: ProgressCallback = None) -> IndexAnalysisContext:
        ctx = IndexAnalysisContext(target=target)

        # 预计算采集步骤
        steps: list[tuple[str, str, Callable]] = []

        def _fetch_price():
            price_result = self._adapter.fetch(
                target.symbol, data_type="index_price",
                index_style=target.index_style
            )
            if price_result:
                ctx.price_data = price_result

        def _fetch_valuation():
            val_result = self._adapter.fetch(
                target.symbol, data_type="index_valuation"
            )
            if val_result:
                ctx.valuation_data = val_result[0]

        def _fetch_capital_flow():
            cf_result = self._adapter.fetch(
                target.symbol, data_type="index_capital_flow",
                index_style=target.index_style
            )
            if cf_result:
                ctx.capital_flow = cf_result[0]

        def _fetch_macro():
            macro_result = self._adapter.fetch(
                target.symbol, data_type="index_macro",
                index_style=target.index_style
            )
            if macro_result:
                ctx.macro = macro_result[0]

        def _fetch_sentiment():
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

        # 所有类别都采集行情和估值
        steps.append(("index_price", INDEX_COLLECT_LABELS["index_price"], _fetch_price))
        steps.append(("index_valuation", INDEX_COLLECT_LABELS["index_valuation"], _fetch_valuation))

        # 按类别采集资金流向
        if target.index_style in ("broad", "sector"):
            steps.append(("index_capital_flow", INDEX_COLLECT_LABELS["index_capital_flow"], _fetch_capital_flow))

        # 按类别采集宏观数据
        if target.index_style in ("broad", "overseas"):
            steps.append(("index_macro", INDEX_COLLECT_LABELS["index_macro"], _fetch_macro))
        elif target.index_style == "sector":
            from data.schemas import MacroContext
            from datetime import date
            ctx.macro = MacroContext(symbol=target.symbol, fetch_date=date.today())

        # 舆情（所有类别）
        steps.append(("index_sentiment", INDEX_COLLECT_LABELS["index_sentiment"], _fetch_sentiment))

        total = len(steps)
        for i, (_data_type, label, fetch_fn) in enumerate(steps):
            try:
                fetch_fn()
            except Exception as e:
                logger.warning(f"采集 {label} 失败: {e}")
            if on_progress:
                on_progress("collect", i + 1, total, label)

        return ctx

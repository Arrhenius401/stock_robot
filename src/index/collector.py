"""IndexDataCollector — 按 index_style 编排指数数据采集，只拉取原始数据"""
import logging
from collections.abc import Callable

from core.pipeline import ProgressCallback
from core.registry import Registry
from data.akshare import AkShareAdapter
from data.schemas import AnalysisTarget, IndexAnalysisContext

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
        ctx = IndexAnalysisContext(target=target, requested_instrument=target.requested_instrument)
        if target.index_style == "strategy":
            return self._collect_strategy(ctx, on_progress)

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
            from datetime import datetime

            from data.schemas import MacroContext
            ctx.macro = MacroContext(symbol=target.symbol, fetch_date=datetime.now().astimezone().date())

        # 舆情（所有类别）
        steps.append(("index_sentiment", INDEX_COLLECT_LABELS["index_sentiment"], _fetch_sentiment))

        total = len(steps)
        for i, (_data_type, label, fetch_fn) in enumerate(steps):
            try:
                fetch_fn()
            except Exception as e:  # noqa: BLE001 — 单项采集失败不影响其余维度
                logger.warning(f"采集 {label} 失败: {e}")
            if on_progress:
                on_progress("collect", i + 1, total, label)

        return ctx


    def _collect_strategy(self, ctx: IndexAnalysisContext, on_progress: ProgressCallback) -> IndexAnalysisContext:
        """策略仅访问适配的真实数据源，不走旧宽基估值或资金流接口。"""
        from data.index_mapping import IndexMapping
        from index.valuation_data import StrategyValuationProvider

        provider = StrategyValuationProvider()
        entry = IndexMapping().lookup(ctx.target.symbol)
        price_provider = entry.provider if entry and entry.provider else "csi"
        ctx.price_data = provider.fetch_prices(ctx.target.symbol, provider=price_provider)
        total = 5 if ctx.requested_instrument else 4
        if on_progress:
            on_progress("collect", 1, total, "采集策略指数价格行情")
        if not ctx.price_data:
            ctx.risk_flags.append("官方与独立回退行情源均未取得真实价格")
            return ctx
        ctx.strategy_data = provider.collect(ctx.target)
        ctx.risk_flags.extend(ctx.strategy_data.errors)
        if on_progress:
            on_progress("collect", 2, total, "采集官方权重及年度专项数据")
        try:
            ctx.valuation_data = provider.fetch(ctx.target.symbol, provider=price_provider)
        except Exception as exc:  # noqa: BLE001 — 估值数据源隔离，保留专项与行情分析
            logger.warning("策略指数 %s 官方估值采集失败: %s", ctx.target.symbol, exc)
            ctx.risk_flags.append("公开官方估值采集失败，稍后可重试")
        if on_progress:
            on_progress("collect", 3, total, "采集公开官方估值及历史PE")
        benchmark = entry.base_index if entry and entry.base_index else "000300"
        ctx.benchmark_prices = provider.fetch_prices(benchmark, provider="csi")
        if not ctx.benchmark_prices:
            ctx.risk_flags.append("同日基准行情不可用，不能计算超额收益")
        if on_progress:
            on_progress("collect", 4, total, "采集对比基准价格行情")
        if ctx.requested_instrument:
            symbol = str(ctx.requested_instrument.get("symbol", ""))
            if symbol:
                ctx.etf_prices = provider.fetch_etf_prices(symbol)
            if not ctx.etf_prices:
                ctx.risk_flags.append("ETF自身前复权行情不可用；指数价格表现不能替代ETF表现")
            if on_progress:
                on_progress("collect", 5, total, "采集ETF独立前复权行情")
        return ctx

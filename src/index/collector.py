"""IndexDataCollector — 按 index_style 编排指数数据采集，只拉取原始数据"""
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from core.pipeline import ProgressCallback
from core.registry import Registry
from data.akshare import AkShareAdapter
from data.base import CollectionResult
from data.collection_policies import index_policy_for
from data.collection_state import make_record
from data.schemas import AnalysisTarget, IndexAnalysisContext
from utils.numbers import is_positive_finite

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

    def __init__(self, registry: Registry | None = None, *, isolate_requests: bool = True, cache_requests: bool | None = None):
        self.isolate_requests = isolate_requests
        self.cache_requests = isolate_requests if cache_requests is None else cache_requests
        self.checkpoint_path: Path | None = None
        self._adapter = AkShareAdapter()
        if registry is not None:
            self._registry = registry
        else:
            self._registry = Registry()
            self._registry.register_data_source(self._adapter)

    def _fetch_result(self, symbol: str, *, data_type: str, **kwargs) -> CollectionResult:
        if not self.cache_requests:
            return self._adapter.fetch_result(symbol, data_type=data_type, **kwargs)
        from index.request_cache import IndexRequestCache
        from utils.config import Config
        return IndexRequestCache(Config().config_dir / "index_collection_cache.db").fetch_cached(self._adapter, symbol, data_type, **kwargs)

    def _checkpoint(self, ctx: IndexAnalysisContext) -> None:
        if self.checkpoint_path is not None:
            temporary = self.checkpoint_path.with_suffix(".part")
            temporary.write_text(ctx.model_dump_json(), encoding="utf-8")
            temporary.replace(self.checkpoint_path)

    def collect(self, target: AnalysisTarget, on_progress: ProgressCallback = None) -> IndexAnalysisContext:
        # 可注入测试适配器无需跨进程，真实适配器在可终止进程内运行。
        if self.isolate_requests:
            from index.collection_budget import collect_bounded
            ctx = collect_bounded(target, on_progress=on_progress)
            if on_progress:
                on_progress("collect", 1, 1, "指数数据采集完成")
            return ctx
        ctx = IndexAnalysisContext(target=target, requested_instrument=target.requested_instrument)
        if target.index_style == "strategy":
            return self._collect_strategy(ctx, on_progress)

        # 预计算采集步骤
        steps: list[tuple[str, str, Callable]] = []

        def record(kind: str, result: CollectionResult) -> list:
            policy = index_policy_for(kind)
            assert policy is not None
            state = policy.classify(target.symbol, result.rows, result.record, None, datetime.now().astimezone())
            source = result.record.source if result.record and result.record.source else ""
            if result.rows:
                row = result.rows[0]
                source = getattr(row, "_data_source", "") or source
                if kind == "index_valuation":
                    urls = [getattr(row, key, "") for key in ("pe_source_url", "pb_source_url", "pe_snapshot_source_url")]
                    source = "；".join(dict.fromkeys(url for url in urls if url)) or source
            if not source or source in {"AkShare", "来源未记录", "未记录"}:
                source = {"index_macro": "AkShare宏观PMI／银行间利率接口", "index_capital_flow": "东方财富沪深港通资金接口", "index_sentiment": "；".join(dict.fromkeys(str(item.get("source") or "来源未记录") for row in result.rows for item in getattr(row, "index_items", [])))}.get(kind) or "来源未记录"
            ctx.collection_records[kind] = make_record(
                source, state, diagnostic=result.record,
                collected_at=result.record.collected_at if result.record and result.record.collected_at else datetime.now().astimezone(),
                cache_status=result.record.cache_status if result.record else "live",
                error_kind="empty" if state == "failed" and not result.record else "")
            return result.rows

        def _fetch_price():
            price_result = record("index_price", self._fetch_result(
                target.symbol, data_type="index_price",
                index_style=target.index_style
            ))
            if price_result:
                ctx.price_data = price_result

        def _fetch_valuation():
            val_result = record("index_valuation", self._fetch_result(
                target.symbol, data_type="index_valuation"
            ))
            if val_result:
                ctx.valuation_data = val_result[0]

        def _fetch_capital_flow():
            cf_result = record("index_capital_flow", self._fetch_result(
                target.symbol, data_type="index_capital_flow",
                index_style=target.index_style
            ))
            if cf_result:
                ctx.capital_flow = cf_result[0]

        def _fetch_macro():
            macro_result = record("index_macro", self._fetch_result(
                target.symbol, data_type="index_macro",
                index_style=target.index_style
            ))
            if macro_result:
                ctx.macro = macro_result[0]

        def _fetch_sentiment():
            sentiment_result = record("index_sentiment", self._fetch_result(
                target.symbol, data_type="index_sentiment"
            ))
            if sentiment_result and len(sentiment_result) > 0:
                news = sentiment_result[0]
                ctx.news_items = news.index_items or [
                    {"title": title, "source": "市场新闻", "published_at": None, "url": ""}
                    for title in news.headlines[:10]
                ]

        # 所有类别都采集行情和估值
        steps.append(("index_price", INDEX_COLLECT_LABELS["index_price"], _fetch_price))
        steps.append(("index_valuation", INDEX_COLLECT_LABELS["index_valuation"], _fetch_valuation))

        # 按类别采集资金流向
        if target.index_style in ("broad", "sector"):
            steps.append(("index_capital_flow", INDEX_COLLECT_LABELS["index_capital_flow"], _fetch_capital_flow))

        # 按类别采集宏观数据
        if target.index_style == "broad":
            steps.append(("index_macro", INDEX_COLLECT_LABELS["index_macro"], _fetch_macro))
        elif target.index_style == "sector":
            from data.schemas import MacroContext
            ctx.macro = MacroContext(symbol=target.symbol, fetch_date=datetime.now().astimezone().date())

        if target.index_style == "sector":
            def _fetch_benchmark():
                ctx.benchmark_prices = self._fetch_result("000300", data_type="index_price", index_style="broad").rows
            steps.append(("benchmark", "采集同日比较基准", _fetch_benchmark))

        # 舆情（国内市场背景，不冒充海外指数专属新闻）
        if target.index_style != "overseas":
            steps.append(("index_sentiment", INDEX_COLLECT_LABELS["index_sentiment"], _fetch_sentiment))

        total = len(steps)
        for i, (data_type, label, fetch_fn) in enumerate(steps):
            try:
                fetch_fn()
                if self.checkpoint_path is not None:
                    self._checkpoint(ctx)
            except Exception as e:  # noqa: BLE001 — 单项采集失败不影响其余维度
                logger.warning(f"采集 {label} 失败: {e}")
                ctx.collection_records[data_type] = make_record(
                    "AkShare", "failed", collected_at=datetime.now().astimezone(),
                    error_kind="fetch_failed", error_message="指数采集失败")
            self._checkpoint(ctx)
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
        def strategy_record(kind: str, available: bool, errors: list[str] | None = None) -> None:
            state = "failed" if not available else "partial" if errors else "complete"
            source = ctx.strategy_data.source if kind == "strategy_data" and ctx.strategy_data else f"{price_provider}官方指数数据"
            price_rows = ctx.price_data if kind == "index_price" else ctx.benchmark_prices if kind == "benchmark" else []
            if price_rows:
                source = getattr(price_rows[0], "_data_source", "") or "原始行情来源未记录"
            if kind == "index_valuation" and ctx.valuation_data:
                source = "；".join(dict.fromkeys(value for value in (ctx.valuation_data.pe_source_url, ctx.valuation_data.pb_source_url, ctx.valuation_data.pe_snapshot_source_url) if value)) or "估值来源未记录"
            ctx.collection_records[kind] = make_record(
                source, state, collected_at=datetime.now().astimezone(),
                error_kind="empty" if not available else "",
                error_message="；".join(errors or []))
            self._checkpoint(ctx)

        try:
            ctx.price_data = provider.fetch_prices(ctx.target.symbol, provider=price_provider)
            if self.checkpoint_path is not None:
                self._checkpoint(ctx)
        except Exception:  # 保留提供器异常的原有传播语义
            logger.warning("策略采集 index_price 失败", exc_info=True)
            strategy_record("index_price", False)
            raise
        strategy_record("index_price", bool(ctx.price_data))
        total = 5 if ctx.requested_instrument else 4
        if on_progress:
            on_progress("collect", 1, total, "采集策略指数价格行情")
        if not ctx.price_data:
            ctx.risk_flags.append("官方与独立回退行情源均未取得真实价格")
            return ctx
        try:
            ctx.valuation_data = provider.fetch(ctx.target.symbol, provider=price_provider)
            valuation = ctx.valuation_data
            available = valuation is not None and any(
                is_positive_finite(value) for value in (valuation.pe_ttm, valuation.pb, valuation.pe_snapshot))
            degradation = [] if valuation is None else [
                note for note in valuation.valuation_notes
                if any(marker in note for marker in ("失败", "不可用", "过期", "缺失", "未披露", "拒绝", "不足", "未提供估值"))
            ]
            strategy_record("index_valuation", available, degradation)
        except Exception as exc:  # noqa: BLE001 — 估值数据源隔离，保留专项与行情分析
            logger.warning("策略指数 %s 官方估值采集失败: %s", ctx.target.symbol, exc)
            ctx.risk_flags.append("公开官方估值采集失败，稍后可重试")
            strategy_record("index_valuation", False)
        if on_progress:
            on_progress("collect", 2, total, "采集公开官方估值及历史PE")
        try:
            ctx.strategy_data = provider.collect(ctx.target)
            if self.checkpoint_path is not None:
                self._checkpoint(ctx)
        except Exception:  # 保留提供器异常的原有传播语义
            logger.warning("策略采集 strategy_data 失败", exc_info=True)
            strategy_record("strategy_data", False)
            raise
        ctx.risk_flags.extend(ctx.strategy_data.errors)
        strategy_record("strategy_data", bool(ctx.strategy_data.members), ctx.strategy_data.errors)
        if on_progress:
            on_progress("collect", 3, total, "采集官方权重及年度专项数据")
        benchmark = entry.base_index if entry and entry.base_index else "000300"
        try:
            ctx.benchmark_prices = provider.fetch_prices(benchmark, provider="csi")
        except Exception:  # 保留提供器异常的原有传播语义
            logger.warning("策略采集 benchmark 失败", exc_info=True)
            strategy_record("benchmark", False)
            raise
        strategy_record("benchmark", bool(ctx.benchmark_prices))
        if not ctx.benchmark_prices:
            ctx.risk_flags.append("同日基准行情不可用，不能计算超额收益")
        if on_progress:
            on_progress("collect", 4, total, "采集对比基准价格行情")
        if ctx.requested_instrument:
            symbol = str(ctx.requested_instrument.get("symbol", ""))
            if symbol:
                try:
                    ctx.etf_prices = provider.fetch_etf_prices(symbol)
                except Exception:  # 保留提供器异常的原有传播语义
                    logger.warning("策略采集 ETF 失败", exc_info=True)
                    strategy_record("etf", False)
                    raise
            strategy_record("etf", bool(ctx.etf_prices))
            if not ctx.etf_prices:
                ctx.risk_flags.append("ETF自身前复权行情不可用；指数价格表现不能替代ETF表现")
            if on_progress:
                on_progress("collect", 5, total, "采集ETF独立前复权行情")
        return ctx

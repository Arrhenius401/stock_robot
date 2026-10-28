"""管道调度器 — 串联数据采集→分析→LLM 解读→报告输出的完整流程"""
import json
import logging
import math
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from core.circuit_breaker import CircuitBreaker
from core.collection_contract import DEDICATED_CAPABILITIES, MIGRATED_CAPABILITIES
from core.registry import Registry
from data.base import CollectionResult, DataSource
from data.cache import CacheManager
from data.collection_policies import dedicated_policy_for
from data.collection_state import make_record, record_consistent, with_result_state
from data.hk_news import HK_TZ
from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    AnalysisTarget,
    DataCollectionRecord,
    SufficiencyLevel,
)
from data.validation import financial_has_facts
from report.formatter import format_metrics
from utils.config import Config
from utils.numbers import is_finite_number, is_positive_finite

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int, str], None] | None

DATA_TYPE_LABELS = {
    "financial": "采集财务数据",
    "price": "采集价格数据",
    "valuation": "采集估值数据",
    "industry": "采集行业数据",
    "news": "采集舆情数据",
}

DIMENSION_LABELS = {
    "financial": "财务分析",
    "technical": "技术面分析",
    "valuation": "估值分析",
    "industry": "行业分析",
    "sentiment": "舆情分析",
}

DIMENSION_LLM_LABELS = {
    "financial": "生成财务解读",
    "technical": "生成技术面解读",
    "valuation": "生成估值解读",
    "industry": "生成行业解读",
    "sentiment": "生成舆情解读",
}

# 分析维度到所需数据类型的映射
DATA_TYPES = ["financial", "price", "valuation", "industry", "news"]


def _cacheable(market: str, data_type: str) -> bool:
    """同行证据使用专用版本化缓存，避免复用旧池或丢失私有估值字段。"""
    return data_type != "industry"
DIMENSION_DATA_MAP = {
    "financial": ["financial"],
    "technical": ["price"],
    "valuation": ["price", "valuation"],
    "industry": ["industry"],
    "sentiment": ["news"],
}

# 数据类型到上下文属性的映射
DATA_TYPE_ATTRS = {
    "price": "price_data",
    "financial": "financial_data",
    "valuation": "valuation_data",
    "industry": "industry_data",
    "news": "news_data",
}

# TTL 键映射
TTL_KEY_MAP = {
    "price": "daily", "valuation": "daily",
    "financial": "quarterly", "industry": "quarterly",
    "news": "news",
}

# Schema 类型映射（用于反序列化缓存）
SCHEMA_CLASS_MAP = {}

# 港股估值降级结果短 TTL：尽快等待下次实时采集补全，不用整天复用残缺快照
HK_VALUATION_DEGRADED_TTL = 1800


class Pipeline:
    def __init__(self, registry: Registry, config: Config | None = None,
                 llm_enabled: bool | None = None):
        self._registry = registry
        self._config = config or Config()
        if llm_enabled is None:
            llm_enabled = self._config.get("llm.enabled", True)
        self._llm_enabled = llm_enabled
        cache_db = self._config.config_dir / "cache.db"
        self._cache = CacheManager(db_path=cache_db)
        # 只串行化同一管道内港股新闻的保护性读写，不锁网络采集或其他市场。
        self._hk_news_cache_lock = Lock()
        self._hk_valuation_cache_lock = Lock()

        # 内存断路器：per (symbol, data_type) 失败计数，源头故障时跳过请求
        self._breaker = CircuitBreaker()
        from core.resilient_collection import ResilientCollector

        self._resilient_collector = ResilientCollector(self._cache, self._registry, self._breaker, self._config)

        # 新增：行业分类器 + 配置加载器
        from analysis.config_loader import ConfigLoader
        from data.industry_classifier import IndustryClassifier
        self._classifier = IndustryClassifier()
        self._config_loader = ConfigLoader()
        self._validate_dedicated_contract()

    def _validate_dedicated_contract(self) -> None:
        for market, kind in DEDICATED_CAPABILITIES:
            sources = self._registry.get_data_sources(market, kind)
            if not isinstance(sources, list):
                continue
            for source in sources:
                if type(source).fetch_result is DataSource.fetch_result:
                    raise ValueError(
                        f"数据源 {source.__class__.__name__} 未实现统一取数入口 fetch_result"
                    )

    def collect(self, symbol: str, name: str, market: str = "a-shares",
                refresh_cache: bool = False, data_types: list[str] | None = None,
                on_progress: ProgressCallback = None) -> AnalysisContext:
        ctx = AnalysisContext(symbol=symbol, name=name, market=market)

        # 申万分类必须先于行业成分股采集确定，保证同业池与报告口径一致。
        classification = self._classifier.lookup(symbol) if market == "a-shares" else None
        if classification is not None:
            ctx.sw_industry = classification.sw_level1
            ctx.sw_industry_level2 = classification.sw_level2
            ctx.style_category = classification.style_category
        if classification is not None and not classification.is_verified:
            try:
                from data.industry_mapping_builder import update_symbol
                updated = update_symbol(symbol, retries=1, timeout=5)
                ctx.sw_industry = updated["sw_level1"]
                ctx.sw_industry_level2 = updated["sw_level2"]
                ctx.style_category = updated["style_category"]
            except Exception:  # noqa: BLE001 — 回填失败不阻断分析流程
                logger.debug("申万行业映射反查失败 %s", symbol)

        types_to_fetch = data_types or DATA_TYPES
        total = len(types_to_fetch)
        completed = 0

        def fetch_one(data_type: str, stagger_index: int):
            # 递增错峰：第 n 个线程延迟 n*0.15s，减轻上游瞬时压力
            time.sleep(stagger_index * 0.15)
            from core.resilient_collection import Kind, supports

            if supports(market, data_type):
                from typing import cast

                outcome = self._resilient_collector.collect(symbol, market, cast(Kind, data_type), refresh_cache)
                return data_type, outcome.rows, outcome.record, outcome.reference
            if (market, data_type) in MIGRATED_CAPABILITIES:
                logger.error("已迁移能力不得走旧取数路径 %s/%s", market, data_type)
                return data_type, None, DataCollectionRecord(
                    cache_status="live", cache_state="degraded", result_state="failed",
                    error_kind="contract_error",
                    error_message="已迁移能力不得走旧取数路径",
                )
            if market == "hk" and data_type == "news":
                result, record = self._collect_hk_news(symbol, name, refresh_cache)
                return data_type, result, record
            cacheable = _cacheable(market, data_type)
            policy = dedicated_policy_for(market, data_type)
            record = DataCollectionRecord()
            raw_record = self._cache.get_metadata(data_type, symbol, "latest")
            if raw_record:
                try:
                    record = DataCollectionRecord.model_validate_json(raw_record)
                except ValueError:
                    logger.debug("缓存采集记录损坏 %s/%s", symbol, data_type)
            cached_record = record
            stale_record = cached_record.model_copy(update={
                "cache_status": "stale", "error_kind": "live_unavailable",
                "error_message": "实时数据不可用，使用过期缓存",
            })
            failure_record = make_record("", "failed", error_kind="empty", error_message="数据源未返回可用数据")
            stale_cached = None
            if ((data_type == "news" and not refresh_cache)
                    or (market == "hk" and data_type == "valuation")):
                stale_cached = self._get_stale_cached(symbol, data_type)
                if market == "hk" and data_type == "valuation" and stale_cached is None:
                    stale_cached = self._get_stale_cached(symbol, data_type, date_key="last_complete")
                    raw_stale_record = self._cache.get_metadata(data_type, symbol, "last_complete")
                    if raw_stale_record:
                        try:
                            stale_record = DataCollectionRecord.model_validate_json(raw_stale_record).model_copy(
                                update={"cache_status": "stale", "error_kind": "live_unavailable",
                                        "error_message": "实时数据不可用，使用上次完整缓存"})
                        except ValueError:
                            logger.debug("港股完整估值的采集记录损坏 %s", symbol)
            if stale_cached is not None and policy is not None and stale_record.result_state == "unknown":
                stale_record = with_result_state(
                    stale_record, policy.classify(symbol, stale_cached, None, None, datetime.now(HK_TZ))
                )
            # 本地缓存读取不触达上游，优先于断路器——断路器打开期间缓存健康数据仍可用
            if not refresh_cache and cacheable:
                cached = self._get_cached(symbol, data_type)
                if cached is not None:
                    self._breaker.record_success(symbol, data_type)
                    if policy is not None and record.result_state == "unknown":
                        record = with_result_state(
                            record, policy.classify(symbol, cached, None, None, datetime.now(HK_TZ))
                        )
                    return data_type, cached, record.model_copy(update={"cache_status": "cache"})
            breaker_open = self._breaker.is_open(symbol, data_type)
            if breaker_open and data_type != "industry":
                logger.warning(f"断路器打开: {symbol}/{data_type}，跳过源头请求")
                if stale_cached is not None:
                    logger.warning(f"{symbol}/{data_type} 断路器打开，使用过期缓存兜底")
                    return data_type, stale_cached, stale_record
                return data_type, None, failure_record

            sources = self._registry.get_data_sources(market, data_type)
            for source in sources:
                if type(source).fetch_result is DataSource.fetch_result:
                    return data_type, None, make_record(
                        source.__class__.__name__, "failed", error_kind="contract_error",
                        error_message="数据源未实现统一取数入口 fetch_result",
                    )
                for attempt in range(1 if data_type == "industry" else 2):
                    try:
                        fetch_kwargs: dict[str, Any] = {"data_type": data_type, "market": market}
                        if data_type == "industry":
                            fetch_kwargs.update(
                                sw_level1=ctx.sw_industry,
                                sw_level2=ctx.sw_industry_level2,
                                comparison_cache=self._cache,
                                refresh_cache=refresh_cache,
                                comparison_cache_only=breaker_open,
                            )
                        collection = source.fetch_result(symbol, **fetch_kwargs)
                        if not isinstance(collection, CollectionResult):
                            return data_type, None, make_record(
                                source.__class__.__name__, "failed", error_kind="contract_error",
                                error_message="fetch_result 返回类型无效",
                            )
                        result = collection.rows or []
                        diagnostic = collection.record
                        if diagnostic is not None and not record_consistent(diagnostic):
                            return data_type, None, make_record(
                                source.__class__.__name__, "failed", error_kind="contract_error",
                                error_message="取数诊断状态矛盾",
                            )
                        if diagnostic is not None:
                            failure_record = make_record(
                                diagnostic.source or source.__class__.__name__, "failed", diagnostic=diagnostic,
                            )
                        if result:
                            if (data_type == "valuation" and policy is not None
                                    and not policy.validate(symbol, result, datetime.now(HK_TZ))):
                                failure_record = make_record(
                                    source.__class__.__name__, "failed", diagnostic=diagnostic,
                                    error_kind="invalid_data", error_message="估值数据无效或缺失",
                                )
                                if attempt == 0:
                                    time.sleep(1)
                                continue
                            record = DataCollectionRecord(
                                source=getattr(result[0], "_data_source", "") or source.__class__.__name__,
                                collected_at=datetime.now().astimezone(), cache_status="live",
                            )
                            if isinstance(diagnostic, DataCollectionRecord):
                                # 部分报表成功仍须保留补充接口失败，缓存与报告不能抹去缺失原因。
                                record = record.model_copy(update={
                                    "error_kind": diagnostic.error_kind,
                                    "error_message": diagnostic.error_message,
                                })
                            used_stale = False
                            if data_type == "valuation" and market == "hk":
                                merged_result, used_stale = self._merge_hk_valuation(
                                    stale_cached, result)
                                if merged_result is not None:
                                    result = merged_result
                                if used_stale:
                                    record = record.model_copy(update={
                                        "source": getattr(result[0], "_data_source", "") or record.source,
                                        "cache_status": "stale",
                                        "error_kind": "live_unavailable",
                                        "error_message": "估值实时数据不完整，沿用已记录日期的快照或同源历史/PS",
                                    })
                            if data_type == "valuation" and policy is not None:
                                state = policy.classify(symbol, result, diagnostic, None, datetime.now(HK_TZ))
                                if state == "failed":
                                    failure_record = make_record(
                                        source.__class__.__name__, "failed", diagnostic=diagnostic,
                                        error_kind="invalid_data", error_message="估值数据无效或缺失",
                                    )
                                    if attempt == 0:
                                        time.sleep(1)
                                    continue
                                if market == "hk" and used_stale:
                                    state = "partial"
                                record = with_result_state(record, state)
                            if data_type == "price" and not self._is_healthy(data_type, result):
                                logger.warning("行情格式校验失败 %s", symbol)
                                failure_record = DataCollectionRecord(source=record.source,
                                    error_kind="invalid_data", error_message="行情日期或价格格式无效")
                                continue
                            if data_type == "industry":
                                if policy is not None:
                                    state = policy.classify(symbol, result, diagnostic, None, datetime.now(HK_TZ))
                                    record = with_result_state(record, state)
                                # 资格池可用于经营比较，但仅读缓存或报价失败不代表上游恢复。
                                if not breaker_open:
                                    if (self._is_healthy(data_type, result, symbol)
                                            and not result[0].comparison_snapshot.get("errors")):
                                        self._breaker.record_success(symbol, data_type)
                                    else:
                                        self._breaker.record_failure(symbol, data_type)
                                return data_type, result, record
                            if self._is_healthy(data_type, result, symbol):
                                if cacheable:
                                    self._set_cache(symbol, data_type, result, record)
                                self._breaker.record_success(symbol, data_type)
                            else:
                                # 退化结果：不落库（_set_cache 门控双保险），计失败促断路器
                                if cacheable:
                                    self._set_cache(symbol, data_type, result, record)
                                if (market == "hk" and data_type == "valuation"
                                        and any(is_finite_number(value) for value in
                                                (result[0].pe_ttm, result[0].pb, result[0].ps_ttm))):
                                    # 合法非正值仅不适合写成功缓存，不代表数据源不可用。
                                    self._breaker.record_success(symbol, data_type)
                                else:
                                    self._breaker.record_failure(symbol, data_type)
                                if data_type == "news" and stale_cached is not None:
                                    logger.warning(f"{symbol}/{data_type} 实时数据退化，使用过期缓存兜底")
                                    self._breaker.record_success(symbol, data_type)
                                    return data_type, stale_cached, stale_record
                            return data_type, result, record
                    except Exception as e:  # noqa: BLE001 — 多数据源逐个尝试，单源失败降级
                        logger.warning(f"数据源 {source.__class__.__name__} 获取 {data_type} 失败: {e}")
                        kind = "timeout" if isinstance(e, TimeoutError) or "Timeout" in type(e).__name__ else "fetch_failed"
                        failure_record = make_record(source.__class__.__name__, "failed",
                            error_kind=kind, error_message="数据请求超时" if kind == "timeout" else "数据请求失败")
                    if attempt == 0 and data_type != "industry":
                        time.sleep(1)  # 重试前等待 1 秒
            if not (data_type == "industry" and breaker_open):
                self._breaker.record_failure(symbol, data_type)
            if stale_cached is not None:
                logger.warning(f"{symbol}/{data_type} 实时采集失败，使用过期缓存兜底")
                self._breaker.record_success(symbol, data_type)
                return data_type, stale_cached, stale_record
            return data_type, None, failure_record

        stagger_counter = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {}
            for dt in types_to_fetch:
                futures[executor.submit(fetch_one, dt, stagger_counter)] = dt
                stagger_counter += 1
            for future in as_completed(futures):
                collected = future.result()
                data_type, result, record = collected[:3]
                if len(collected) == 4 and collected[3] is not None:
                    ctx.reference_data[data_type] = collected[3]
                ctx.collection_records[data_type] = record
                if result is not None:
                    self._assign_to_context(ctx, data_type, result)
                    if data_type == "industry" and not ctx.sw_industry:
                        ctx.sw_industry = result[0].resolved_sw_level1
                        ctx.sw_industry_level2 = result[0].resolved_sw_level2
                completed += 1
                if on_progress:
                    on_progress("collect", completed, total,
                               DATA_TYPE_LABELS.get(data_type) or data_type)

        return ctx

    def _collect_hk_news(self, symbol: str, name: str, refresh: bool) -> tuple[list | None, DataCollectionRecord]:
        """港股关键词检索不计情绪分，单次采集的两请求预算不被外层重试放大。"""
        from data.hk_news import SOURCE

        policy = dedicated_policy_for("hk", "news")
        assert policy is not None
        previous = self._get_stale_cached(symbol, "news")
        record = DataCollectionRecord(source=SOURCE)
        raw_record = self._cache.get_metadata("news", symbol, "latest")
        if raw_record:
            try:
                record = DataCollectionRecord.model_validate_json(raw_record)
            except ValueError:
                logger.debug("港股新闻缓存采集记录损坏 %s", symbol)
        if previous is not None:
            # 即使旧缓存缺少独立元数据，也只能采用快照内保存的实际采集时刻。
            record = record.model_copy(update={"collected_at": previous[0].hk_news.fetched_at})
            if record.result_state == "unknown":
                record = with_result_state(
                    record, policy.classify(symbol, previous, None, None, datetime.now(HK_TZ))
                )
        if not refresh:
            cached = self._get_cached(symbol, "news")
            if cached is not None:
                return cached, record.model_copy(update={"cache_status": "cache"})

        failure = make_record(SOURCE, "failed", error_kind="fetch_failed", error_message="新闻暂时获取失败")
        if refresh or not self._breaker.is_open(symbol, "news"):
            sources = self._registry.get_data_sources("hk", "news")
            if sources:
                source = sources[0]
                if type(source).fetch_result is DataSource.fetch_result:
                    return None, make_record(SOURCE, "failed", error_kind="contract_error",
                                             error_message="港股新闻来源未实现统一取数入口 fetch_result")
                try:
                    # 此能力只有一个已验证入口；不尝试备用适配器重复发起相同双查询。
                    collection = source.fetch_result(symbol, market="hk", data_type="news", name=name)
                    if not isinstance(collection, CollectionResult):
                        return None, make_record(SOURCE, "failed", error_kind="contract_error",
                                                 error_message="fetch_result 返回类型无效")
                    if collection.record is not None and not record_consistent(collection.record):
                        return None, make_record(SOURCE, "failed", error_kind="contract_error",
                                                 error_message="取数诊断状态矛盾")
                    result = collection.rows or []
                    if collection.record is not None:
                        failure = make_record(SOURCE, "failed", diagnostic=collection.record,
                                              error_kind=collection.record.error_kind or "fetch_failed",
                                              error_message=collection.record.error_message or "新闻暂时获取失败")
                    state = policy.classify(symbol, result, collection.record, None, datetime.now(HK_TZ))
                    if state != "failed" and self._is_healthy("news", result, symbol):
                        snapshot = result[0].hk_news
                        record = make_record(
                            SOURCE, state, diagnostic=collection.record, collected_at=snapshot.fetched_at,
                            error_kind="partial" if snapshot.query_errors else "",
                            error_message="部分关键词未成功检索，结果不完整" if snapshot.query_errors else "",
                        )
                        self._set_cache(symbol, "news", result, record)
                        self._breaker.record_success(symbol, "news")
                        return result, record
                except Exception as error:  # noqa: BLE001 — 港股新闻失败隔离，不影响其他分析维度
                    logger.warning("港股关键词新闻获取失败 %s: %s", symbol, error)
            self._breaker.record_failure(symbol, "news")
        if previous is not None:
            return previous, record.model_copy(update={
                "cache_status": "stale", "error_kind": "live_unavailable",
                "error_message": "本次刷新失败，使用此前缓存；条目仍按近30天过滤",
            })
        return None, failure

    def collect_from_target(self, target: AnalysisTarget) -> AnalysisContext:
        """从 AnalysisTarget 收集数据 — 个股管道的入口适配"""
        return self.collect(
            symbol=target.symbol,
            name=target.name,
            market=target.market,
        )

    def run(self, symbol: str, name: str, market: str = "a-shares",
            dimension: str | None = None, refresh_cache: bool = False,
            on_progress: ProgressCallback = None
            ) -> tuple[list[AnalysisResult], dict[str, str], AnalysisContext]:
        data_types = None
        analysis_modules = self._registry.get_analysis_modules()
        if dimension:
            data_types = DIMENSION_DATA_MAP.get(dimension, DATA_TYPES)
            analysis_modules = [m for m in analysis_modules if m.dimension == dimension]

        ctx = self.collect(symbol, name, market, refresh_cache=refresh_cache,
                           data_types=data_types, on_progress=on_progress)

        # 充实步骤（collect 之后 analyze 之前）
        from data.enricher import ContextEnricher
        from data.enrichers import (
            FinancialEnricher,
            HKIndustryEnricher,
            IndustryEnricher,
            PriceEnricher,
            SentimentEnricher,
            ValuationEnricher,
        )

        enricher = ContextEnricher()
        enricher.register(PriceEnricher())
        enricher.register(FinancialEnricher())
        enricher.register(ValuationEnricher())
        enricher.register(IndustryEnricher())
        enricher.register(HKIndustryEnricher())

        # 主要维度完成确定性充实后再决定是否允许舆情模型调用。
        ctx = enricher.enrich(ctx)
        from data.peer_comparison import PeerComparisonService

        if on_progress and ctx.industry_data is not None and getattr(ctx.industry_data, "comparison_ready", False):
            on_progress("collect", 1, 1, "采集同行估值与同期经营指标")
        try:
            ctx = PeerComparisonService(self._cache).enrich(ctx, refresh=refresh_cache)
        except Exception as error:  # noqa: BLE001 — 同行采集隔离，不能阻断其他报告维度
            from data.schemas import DimensionSufficiency

            logger.warning("同行比较失败 %s: %s", symbol, type(error).__name__)
            ctx.enriched_industry = None
            ctx.sufficiency.industry = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT, reason="同行比较数据获取失败",
                sample_count=0, score_weight=0.0,
            )
        if dimension in (None, "financial"):
            from data.financial_scoring_facts import FinancialScoringFactsService

            try:
                ctx = FinancialScoringFactsService(self._cache).enrich(ctx, refresh=refresh_cache)
            except Exception as error:  # noqa: BLE001 — 年度补取边界失败只跳过缺失子项
                logger.warning("年度财务评分依据获取失败 %s: %s", symbol, type(error).__name__)
                ctx.financial_scoring_facts = {"annual_roe": {"value": None, "reason": "年度依据获取失败"}}
        sentiment_enricher = SentimentEnricher()
        has_main_data = any(
            getattr(ctx.sufficiency, key).level != SufficiencyLevel.INSUFFICIENT
            for key in ("price", "financial", "valuation", "industry")
        )
        if self._llm_enabled and (has_main_data or dimension == "sentiment"):
            provider = self._config.get("llm.provider", "openai")
            sentiment_enricher.set_llm(self._registry.get_llm_backend(provider))
        sentiment_stage = ContextEnricher()
        sentiment_stage.register(sentiment_enricher)
        ctx = sentiment_stage.enrich(ctx)

        # 新增：加载行业配置
        industry_config = self._config_loader.load_for_context(ctx)

        results = []
        total = len(analysis_modules)
        completed = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_map = {
                executor.submit(m.analyze, ctx, industry_config): m
                for m in analysis_modules
            }
            for future in as_completed(future_map):
                mod = future_map[future]
                try:
                    results.append(future.result())
                except Exception as e:  # noqa: BLE001 — 单模块失败不影响其他维度
                    logger.error(f"分析模块 {mod.dimension} 执行失败: {e}")
                    results.append(AnalysisResult(
                        dimension=mod.dimension, status="unavailable",
                        summary="分析模块暂时不可用", metrics={}))
                completed += 1
                if on_progress:
                    on_progress("analyze", completed, total,
                               DIMENSION_LABELS.get(mod.dimension, mod.dimension))

        commentary = {}
        if self._llm_enabled:
            commentary = self._generate_commentary(
                symbol, name, results, ctx.sw_industry, ctx.style_category,
                on_progress=on_progress, context=ctx
            )

        return results, commentary, ctx

    def _generate_commentary(self, symbol: str, name: str,
                             results: list[AnalysisResult],
                             sw_industry: str = "",
                             style_category: str = "",
                             on_progress: ProgressCallback = None,
                             context: AnalysisContext | None = None) -> dict[str, str]:
        """单次批量 LLM 调用，生成四段结构化解读"""
        commentary = {}
        from report.quality import build_data_quality, has_analysis_data
        from report.scoring import compute_score_summary, participation_score

        if not has_analysis_data(results, context):
            return commentary
        provider = self._config.get("llm.provider", "openai")
        llm = self._registry.get_llm_backend(provider)
        if llm is None:
            logger.warning(f"未找到 LLM 后端: provider={provider}")
            return commentary

        # 准备维度得分数据
        dim_labels = {
            "financial": "财务健康", "technical": "技术趋势",
            "valuation": "估值合理", "industry": "行业对比",
            "sentiment": "舆情风险",
        }
        sufficiency_label = {"ok": "充足", "partial": "部分可用", "unavailable": "数据不足"}

        scores = []
        covered = []
        missing = []
        all_risk_flags = []

        for dim, label in dim_labels.items():
            r = None
            for result in results:
                if result.dimension == dim:
                    r = result
                    break
            available = r is not None and r.status in {"ok", "partial"}
            scoring_applicable = True
            scored = participation_score(r) is not None
            scores.append({
                "label": label,
                "score": r.score if available and r and scored else None,
                "direction": "risk" if dim == "sentiment" else "higher_better",
                "sufficiency": sufficiency_label.get(r.status, r.status) if r else "数据不足",
                "score_detail": r.score_detail if r else "",
                "summary": r.summary if r else "",
                "available": available,
                "scoring_applicable": scoring_applicable,
            })
            if available:
                covered.append(label)
                if r:
                    all_risk_flags.extend(r.risk_flags)
            else:
                missing.append(label)

        # 收集行业说明
        industry_note = ""
        for r in results:
            if r.industry_note:
                industry_note = r.industry_note
                break

        try:
            from jinja2 import Environment, FileSystemLoader
            template_dir = Path(__file__).parent.parent / "llm" / "prompt_templates"
            env = Environment(loader=FileSystemLoader(str(template_dir)))

            template = env.get_template("batch_analysis_hk.jinja2" if context is not None and context.market == "hk"
                                        else f"batch_analysis_{provider}.jinja2")
            prompt = template.render(
                sw_industry=sw_industry,
                style_category=style_category,
                industry_note=industry_note,
                name=name, symbol=symbol, scores=scores,
                risk_flags=all_risk_flags,
                score_summary=compute_score_summary(results),
                covered_dims="、".join(covered) if covered else "无",
                missing_dims="、".join(missing) if missing else "无",
                analysis_facts=json.dumps([
                    {"dimension": r.dimension, "status": r.status, "score": r.score,
                     "score_detail": r.score_detail, "metrics": format_metrics(r.metrics),
                     "summary": r.summary, "risk_flags": r.risk_flags}
                    for r in results
                ], ensure_ascii=False, default=str),
                data_quality=json.dumps(build_data_quality(context, results, include_references=False), ensure_ascii=False)
                if context is not None else "",
            )

            if on_progress:
                on_progress("llm", 1, 2, "生成 AI 解读")

            commentary["bulk"] = llm.generate(prompt)

            if on_progress:
                on_progress("llm", 2, 2, "生成 AI 解读")
        except Exception as e:  # noqa: BLE001 — LLM 失败不影响确定性分析结果
            logger.error(f"LLM 解读生成失败: {e}")

        return commentary

    def _get_cached(self, symbol: str, data_type: str) -> list | None:
        if data_type == "news" and symbol.endswith(".HK"):
            # 不调用会删除过期值的 get；保留旧快照供刷新失败兜底。
            results = self._get_stale_cached(symbol, data_type)
            if results is None:
                return None
            snapshot = results[0].hk_news
            age = (datetime.now().astimezone() - snapshot.fetched_at).total_seconds()
            if not 0 <= age <= 3600:
                return None
            results[0]._from_stale_cache = False
            return results
        if data_type == "valuation" and symbol.endswith(".HK"):
            # 新鲜度判断只返回未命中，不删除后续刷新失败仍需使用的旧成功值。
            raw = self._cache.get(data_type, symbol, "latest", preserve_expired=True)
        else:
            raw = self._cache.get(data_type, symbol, "latest")
        if raw is None:
            return None
        try:
            data_list = json.loads(raw)
            results = self._deserialize_cache(data_type, symbol, data_list)
            if not self._is_healthy(data_type, results, symbol):
                logger.info(f"缓存 {data_type}/{symbol} 数据退化，视为未命中")
                return None
            if (data_type == "industry" and results
                    and not getattr(results[0], "peer_scope", "")):
                # 旧缓存使用东财行业构建同业池，不能进入申万口径评分。
                self._cache.invalidate(data_type, symbol, "latest")
                return None
            if (data_type == "industry" and results
                    and getattr(results[0], "peers", [])
                    and not getattr(results[0], "peer_names", {})):
                # 旧快照未保存同行名称，刷新后才能以“公司名（代码）”展示。
                self._cache.invalidate(data_type, symbol, "latest")
                return None
            return results
        except (ValueError, TypeError, KeyError, AttributeError):
            logger.debug("缓存格式无效 %s/%s", symbol, data_type)
            return None

    def _get_stale_cached(self, symbol: str, data_type: str, *, date_key: str = "latest") -> list | None:
        """读取过期缓存；仅用于外部短时效数据源失败后的兜底。"""
        raw = self._cache.get_stale(data_type, symbol, date_key)
        if raw is None:
            return None
        try:
            data_list = json.loads(raw)
            results = self._deserialize_cache(data_type, symbol, data_list)
            has_hk_facts = (data_type == "valuation" and symbol.endswith(".HK") and results
                            and any(is_finite_number(value) for value in
                                    (results[0].pe_ttm, results[0].pb, results[0].ps_ttm)))
            if not self._is_healthy(data_type, results, symbol) and not has_hk_facts:
                return None
            for item in results:
                if data_type == "news":
                    item._from_stale_cache = True
                    raw_sentiment = getattr(item, "_raw_sentiment", None)
                    if raw_sentiment is not None:
                        raw_sentiment._from_stale_cache = True
            return results
        except (ValueError, TypeError, KeyError, AttributeError):
            logger.debug("过期缓存格式无效 %s/%s", symbol, data_type)
            return None

    @staticmethod
    def _merge_hk_valuation(previous: list | None,
                            current: list | None) -> tuple[list | None, bool]:
        """沿用可信旧证据；较早曲线末值不能覆盖较新快照，不改当前真实快照。"""
        if not previous or not current or not current[0].cache_degraded:
            return current, False
        old, new = previous[0], current[0]
        if old.symbol != new.symbol:
            return current, False
        new = new.model_copy(deep=True)
        failures = new.failed_components
        # 兼容旧调用方仅设置总体降级标记的形态；新采集按具体失败组件补位。
        legacy_failure = not failures
        used_stale = False
        if "曲线末值" in new._data_source:
            for key, label, period_key in (("pe_ttm", "PE(TTM)", "pe_as_of"), ("pb", "PB", "pb_as_of")):
                old_period = getattr(old, period_key)
                new_period = getattr(new, period_key)
                if (is_finite_number(getattr(old, key)) and old_period is not None
                        and (new_period is None or new_period < old_period)):
                    setattr(new, key, getattr(old, key))
                    setattr(new, period_key, old_period)
                    new.valuation_notes.append(f"{label} 曲线末值早于已知快照，沿用截至 {old_period} 的快照值")
                    used_stale = True
            if used_stale:
                new._data_source += "+上次较新快照"
                new.date = max(period for period in (new.pe_as_of, new.pb_as_of) if period is not None)
        used_history = False
        if (old.history_source == "百度港股估值（近一年）"
                and new.history_source in ("", old.history_source)):
            if (not new.pe_history and old.pe_history
                    and (legacy_failure or "pe_history" in failures)):
                new.pe_history = dict(old.pe_history)
                used_history = True
            if (not new.pb_history and old.pb_history
                    and (legacy_failure or "pb_history" in failures)):
                new.pb_history = dict(old.pb_history)
                used_history = True
            if used_history:
                used_stale = True
                new.history_source = new.history_source or old.history_source
                periods = [max(points) for points in (new.pe_history, new.pb_history) if points]
                new.valuation_notes.append(f"历史曲线沿用上次成功数据（各曲线末日：{'、'.join(map(str, periods))}）")
        if (new.ps_ttm is None and old.ps_ttm is not None
                and old.ps_as_of is not None and old.ps_as_of <= datetime.now().astimezone().date()
                and old.ps_source == "东方财富港股估值对比"
                and (legacy_failure or "comparison" in failures)):
            new.ps_ttm, new.ps_as_of, new.ps_source = (
                old.ps_ttm, old.ps_as_of, old.ps_source)
            used_stale = True
            new.valuation_notes.append(f"PS 沿用上次成功数据（截至 {old.ps_as_of}，{old.ps_source}）")
        return [new], used_stale

    def _is_healthy(self, data_type: str, data: list, symbol: str | None = None) -> bool:
        """健康判据：退化结果（空/未知/全缺失）不写持久缓存"""
        if not data:
            return False
        if data_type == "industry":
            ind = data[0]
            if getattr(ind, "industry", "") in ("", "未知"):
                return False
        elif data_type == "financial":
            return financial_has_facts(data, symbol)
        elif data_type == "price":
            for item in data:
                values = (item.open, item.high, item.low, item.close, item.volume)
                if (not isinstance(item.trade_date, date)
                        or not all(math.isfinite(value) and value >= 0 for value in values)
                        or min(item.open, item.close, item.low) <= 0
                        or item.high < max(item.open, item.close, item.low)
                        or item.low > min(item.open, item.close)):
                    return False
        elif data_type == "valuation":
            is_hk = (symbol or "").endswith(".HK") or all(
                str(getattr(v, "symbol", "")).endswith(".HK") for v in data)
            if is_hk:
                # 港股允许负 PE/PB 作为事实保留，但至少要有一项正值估值倍数
                return any(
                    is_positive_finite(value)
                    for v in data
                    for value in (v.pe_ttm, v.pb, v.ps_ttm)
                )
            if all(v.pe_ttm is None and v.pb is None for v in data):
                return False
        elif data_type == "news":
            news = data[0]
            if (symbol or news.symbol).endswith(".HK"):
                snapshot = getattr(news, "hk_news", None)
                return (snapshot is not None and snapshot.fetched_at.utcoffset() is not None
                        and snapshot.fetched_at <= datetime.now().astimezone())
            raw_sentiment = getattr(news, "_raw_sentiment", None)
            if raw_sentiment is not None:
                return len(raw_sentiment.items) >= 3
            return len(getattr(news, "headlines", []) or []) >= 3
        return True

    def _set_cache(self, symbol: str, data_type: str, data: list,
                   record: DataCollectionRecord | None = None):
        if record is not None and (not record_consistent(record) or record.error_kind == "contract_error"):
            logger.error("拒绝缓存不一致或契约错误的采集记录 %s/%s", symbol, data_type)
            return
        if not self._is_healthy(data_type, data, symbol):
            if (data_type == "valuation" and symbol.endswith(".HK") and data
                    and any(is_finite_number(value) for value in
                            (data[0].pe_ttm, data[0].pb, data[0].ps_ttm))):
                # 只保存最新事实供失败兜底；_get_cached 仍拒绝把非正结果当成功缓存命中。
                with self._hk_valuation_cache_lock:
                    self._store_cache(symbol, data_type, data, record)
            logger.info(f"缓存 {data_type}/{symbol} 不满足成功缓存条件")
            return
        if data_type == "valuation" and symbol.endswith(".HK"):
            with self._hk_valuation_cache_lock:
                # 合并只在 collect 中完成，避免报告值和持久化值分叉。
                self._store_cache(symbol, data_type, data, record)
            return
        if data_type == "news" and symbol.endswith(".HK"):
            # 完整结果写入也须持锁，避免部分结果读空后覆盖并发产生的完整快照。
            with self._hk_news_cache_lock:
                self._store_cache(symbol, data_type, data, record)
            return
        self._store_cache(symbol, data_type, data, record)

    def _store_cache(self, symbol: str, data_type: str, data: list,
                     record: DataCollectionRecord | None = None):
        """写入已验证的数据；港股新闻由调用方持锁覆盖条件读取与落库。"""
        if record is not None and (not record_consistent(record) or record.error_kind == "contract_error"):
            logger.error("拒绝缓存不一致或契约错误的采集记录 %s/%s", symbol, data_type)
            return
        if data_type == "news" and symbol.endswith(".HK") and data[0].hk_news.query_errors:
            previous = self._get_stale_cached(symbol, data_type)
            if previous is not None and not previous[0].hk_news.query_errors:
                # 部分查询成功可展示，但不降低持久化的完整成功快照。
                return
        date_key = "latest"
        try:
            hk_valuation = data_type == "valuation" and symbol.endswith(".HK")
            complete = self._is_healthy(data_type, data, symbol) and not getattr(data[0], "cache_degraded", False)
            if hk_valuation and not complete:
                previous = self._get_stale_cached(symbol, data_type)
                if (previous and not previous[0].cache_degraded
                        and self._is_healthy(data_type, previous, symbol)):
                    # 首次遇到旧版单键缓存时保留原值和原采集时间，无需迁移数据库。
                    self._cache.copy_entry(data_type, symbol, "latest", "last_complete")
            dicts = []
            for item in data:
                d = item.model_dump(mode="json")
                # 保存 model_dump 不包含的私有属性
                if hasattr(item, '_raw_sentiment') and item._raw_sentiment is not None:
                    d['_raw_sentiment'] = item._raw_sentiment.model_dump(mode="json")
                if hasattr(item, '_target_mcap'):
                    d['_target_mcap'] = item._target_mcap
                if hasattr(item, '_target_rank'):
                    d['_target_rank'] = item._target_rank
                dicts.append(d)
            serialized = json.dumps(dicts, ensure_ascii=False, default=str)
            ttl = self._config.get(f"data.cache_ttl.{TTL_KEY_MAP.get(data_type, 'daily')}", 86400)
            if data_type == "news" and symbol.endswith(".HK"):
                ttl = 3600
            if (data_type == "valuation" and symbol.endswith(".HK")
                    and getattr(data[0], "cache_degraded", False)):
                ttl = HK_VALUATION_DEGRADED_TTL if ttl < 0 else min(ttl, HK_VALUATION_DEGRADED_TTL)
            self._cache.put(data_type, symbol, date_key, serialized, ttl_seconds=ttl,
                            metadata=record.model_dump_json() if record else None)
            if hk_valuation:
                if complete:
                    self._cache.copy_entry(data_type, symbol, "latest", "last_complete")
                self._cache.cleanup_old_entries(data_type, symbol, date_key,
                                                preserve_date_keys=("last_complete",))
            else:
                self._cache.cleanup_old_entries(data_type, symbol, date_key)
        except Exception as e:  # noqa: BLE001 — 缓存写入失败不影响分析结果
            logger.warning(f"缓存 {data_type} 失败: {e}")

    def _deserialize_cache(self, data_type: str, symbol: str, data_list: list) -> list:
        from data.schemas import (
            FinancialData,
            IndustryData,
            NewsData,
            PriceData,
            RawSentimentData,
            ValuationData,
        )
        cls_map = {
            "price": PriceData, "financial": FinancialData,
            "valuation": ValuationData, "industry": IndustryData, "news": NewsData,
        }
        cls = cls_map.get(data_type)
        if cls is None:
            return []
        results = []
        for item in data_list:
            raw_sentiment_data = item.pop('_raw_sentiment', None)
            target_mcap = item.pop('_target_mcap', None)
            target_rank = item.pop('_target_rank', None)
            obj = cls(**item)
            if data_type == "news" and symbol.endswith(".HK") and obj.hk_news is not None:
                from data.hk_news import current_news

                obj.hk_news = current_news(obj.hk_news)
                obj.headlines = [entry.title for entry in obj.hk_news.items]
            if raw_sentiment_data:
                obj._raw_sentiment = RawSentimentData(**raw_sentiment_data)
            if target_mcap is not None:
                obj._target_mcap = target_mcap
            if target_rank is not None:
                obj._target_rank = target_rank
            results.append(obj)
        return results

    @staticmethod
    def _assign_to_context(ctx: AnalysisContext, data_type: str, data: list):
        if data_type == "news" and ctx.market == "hk" and data:
            from data.hk_news import current_news

            if data[0].hk_news is None:
                return
            filtered = data[0].model_copy(deep=True)
            filtered.hk_news = current_news(filtered.hk_news)
            filtered.headlines = [item.title for item in filtered.hk_news.items]
            filtered._raw_sentiment = None
            data = [filtered]
        attr = DATA_TYPE_ATTRS.get(data_type)
        if not attr:
            return
        # 单值数据类型直接赋值对象，多值赋值列表
        if data_type in ("valuation", "industry", "news") and len(data) == 1:
            setattr(ctx, attr, data[0])
        else:
            setattr(ctx, attr, data)

        # 处理 news 数据附带的 raw_sentiment
        if data_type == "news" and data and hasattr(data[0], "_raw_sentiment"):
            ctx.raw_sentiment = data[0]._raw_sentiment

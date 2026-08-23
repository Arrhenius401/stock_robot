"""管道调度器 — 串联数据采集→分析→LLM 解读→报告输出的完整流程"""
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from core.circuit_breaker import CircuitBreaker
from core.registry import Registry
from data.cache import CacheManager
from data.schemas import AnalysisContext, AnalysisResult, AnalysisTarget
from utils.config import Config

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

        # 内存断路器：per (symbol, data_type) 失败计数，源头故障时跳过请求
        self._breaker = CircuitBreaker()

        # 新增：行业分类器 + 配置加载器
        from analysis.config_loader import ConfigLoader
        from data.industry_classifier import IndustryClassifier
        self._classifier = IndustryClassifier()
        self._config_loader = ConfigLoader()

    def collect(self, symbol: str, name: str, market: str = "a-shares",
                refresh_cache: bool = False, data_types: list[str] | None = None,
                on_progress: ProgressCallback = None) -> AnalysisContext:
        ctx = AnalysisContext(symbol=symbol, name=name, market=market)
        types_to_fetch = data_types or DATA_TYPES
        total = len(types_to_fetch)
        completed = 0

        def fetch_one(data_type: str, stagger_index: int):
            # 递增错峰：第 n 个线程延迟 n*0.15s，减轻上游瞬时压力
            time.sleep(stagger_index * 0.15)
            # 本地缓存读取不触达上游，优先于断路器——断路器打开期间缓存健康数据仍可用
            if not refresh_cache:
                cached = self._get_cached(symbol, data_type)
                if cached is not None:
                    self._breaker.record_success(symbol, data_type)
                    return data_type, cached
            if self._breaker.is_open(symbol, data_type):
                logger.warning(f"断路器打开: {symbol}/{data_type}，跳过源头请求")
                return data_type, None

            sources = self._registry.get_data_sources(market, data_type)
            for source in sources:
                for attempt in range(2):
                    try:
                        result = source.fetch(symbol, data_type=data_type)
                        if result:
                            self._set_cache(symbol, data_type, result)
                            self._breaker.record_success(symbol, data_type)
                            return data_type, result
                    except Exception as e:  # noqa: BLE001 — 多数据源逐个尝试，单源失败降级
                        logger.warning(f"数据源 {source.__class__.__name__} 获取 {data_type} 失败: {e}")
                    if attempt == 0:
                        time.sleep(1)  # 重试前等待 1 秒
            self._breaker.record_failure(symbol, data_type)
            return data_type, None

        stagger_counter = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {}
            for dt in types_to_fetch:
                futures[executor.submit(fetch_one, dt, stagger_counter)] = dt
                stagger_counter += 1
            for future in as_completed(futures):
                data_type, result = future.result()
                if result is not None:
                    self._assign_to_context(ctx, data_type, result)
                completed += 1
                if on_progress:
                    on_progress("collect", completed, total,
                               DATA_TYPE_LABELS.get(data_type) or data_type)

        # 新增：行业分类查询
        classification = self._classifier.lookup(symbol)
        ctx.sw_industry = classification.sw_level1
        ctx.style_category = classification.style_category

        # 若 CSV 分类为兜底值"综合"，尝试从实时行业数据中获取
        if ctx.sw_industry == "综合" and ctx.industry_data and ctx.industry_data.industry:
            real_industry = ctx.industry_data.industry
            ctx.sw_industry = real_industry
            # 从 申万→大类 映射重新推导 style_category
            mapping = self._config_loader._load_yaml(
                self._config_loader.config_dir / "申万_大类_映射.yaml"
            )
            ctx.style_category = mapping.get(real_industry, "高端制造")

        return ctx

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

        sentiment_enricher = SentimentEnricher()
        if self._llm_enabled:
            provider = self._config.get("llm.provider", "openai")
            llm = self._registry.get_llm_backend(provider)
            sentiment_enricher.set_llm(llm)
        enricher.register(sentiment_enricher)

        ctx = enricher.enrich(ctx)

        # 新增：加载行业配置
        industry_config = self._config_loader.load(ctx.sw_industry)

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
                        summary=f"分析模块异常: {e}", metrics={}))
                completed += 1
                if on_progress:
                    on_progress("analyze", completed, total,
                               DIMENSION_LABELS.get(mod.dimension, mod.dimension))

        commentary = {}
        if self._llm_enabled:
            commentary = self._generate_commentary(
                symbol, name, results, ctx.sw_industry, ctx.style_category,
                on_progress=on_progress
            )

        return results, commentary, ctx

    def _generate_commentary(self, symbol: str, name: str,
                             results: list[AnalysisResult],
                             sw_industry: str = "",
                             style_category: str = "",
                             on_progress: ProgressCallback = None) -> dict[str, str]:
        """单次批量 LLM 调用，生成四段结构化解读"""
        commentary = {}
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
        sufficiency_label = {"sufficient": "充足", "partial": "部分可用", "insufficient": "数据不足"}

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
            if r and r.score is not None:
                scores.append({
                    "label": label, "score": r.score,
                    "sufficiency": sufficiency_label.get(r.status, r.status),
                    "score_detail": r.score_detail,
                })
                covered.append(label)
                all_risk_flags.extend(r.risk_flags)
            elif r and r.status == "partial":
                scores.append({
                    "label": label, "score": r.score,
                    "sufficiency": "部分可用",
                    "score_detail": r.score_detail or "",
                })
                covered.append(label)
            else:
                scores.append({
                    "label": label, "score": None,
                    "sufficiency": "数据不足",
                    "score_detail": "",
                })
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

            template = env.get_template(f"batch_analysis_{provider}.jinja2")
            prompt = template.render(
                sw_industry=sw_industry,
                style_category=style_category,
                industry_note=industry_note,
                name=name, symbol=symbol, scores=scores,
                risk_flags=all_risk_flags,
                covered_dims="、".join(covered) if covered else "无",
                missing_dims="、".join(missing) if missing else "无",
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
        raw = self._cache.get(data_type, symbol, "latest")
        if raw is None:
            return None
        try:
            data_list = json.loads(raw)
            return self._deserialize_cache(data_type, symbol, data_list)
        except Exception:  # noqa: BLE001 — 缓存损坏视为未命中
            return None

    def _is_healthy(self, data_type: str, data: list) -> bool:
        """健康判据：退化结果（空/未知/全缺失）不写持久缓存"""
        if not data:
            return False
        if data_type == "industry":
            ind = data[0]
            if getattr(ind, "industry", "") in ("", "未知"):
                return False
        elif data_type == "financial":
            if all(f.total_equity is None and f.total_assets is None for f in data):
                return False
        elif data_type == "price":
            if len(data) < 60:
                return False
        elif data_type == "valuation":
            if all(v.pe_ttm is None and v.pb is None for v in data):
                return False
        return True

    def _set_cache(self, symbol: str, data_type: str, data: list):
        if not self._is_healthy(data_type, data):
            logger.info(f"缓存 {data_type}/{symbol} 数据退化，跳过持久化")
            return
        date_key = "latest"
        try:
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
            self._cache.put(data_type, symbol, date_key, serialized, ttl_seconds=ttl)
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

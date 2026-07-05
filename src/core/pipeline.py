"""管道调度器 — 串联数据采集→分析→LLM 解读→报告输出的完整流程"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
from datetime import date
from pathlib import Path
from src.data.schemas import AnalysisContext, AnalysisResult
from src.data.cache import CacheManager
from src.core.registry import Registry
from src.utils.config import Config

logger = logging.getLogger(__name__)

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

    def collect(self, symbol: str, name: str, market: str = "a-shares",
                refresh_cache: bool = False, data_types: list[str] | None = None) -> AnalysisContext:
        ctx = AnalysisContext(symbol=symbol, name=name, market=market)
        types_to_fetch = data_types or DATA_TYPES

        def fetch_one(data_type: str):
            if not refresh_cache:
                cached = self._get_cached(symbol, data_type)
                if cached is not None:
                    return data_type, cached

            sources = self._registry.get_data_sources(market, data_type)
            for source in sources:
                try:
                    result = source.fetch(symbol, data_type=data_type)
                    if result:
                        self._set_cache(symbol, data_type, result)
                        return data_type, result
                except Exception as e:
                    logger.warning(f"数据源 {source.__class__.__name__} 获取 {data_type} 失败: {e}")
            return data_type, None

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(fetch_one, dt): dt for dt in types_to_fetch}
            for future in as_completed(futures):
                data_type, result = future.result()
                if result is not None:
                    self._assign_to_context(ctx, data_type, result)

        return ctx

    def run(self, symbol: str, name: str, market: str = "a-shares",
            dimension: str | None = None, refresh_cache: bool = False
            ) -> tuple[list[AnalysisResult], dict[str, str]]:
        data_types = None
        analysis_modules = self._registry.get_analysis_modules()
        if dimension:
            data_types = DIMENSION_DATA_MAP.get(dimension, DATA_TYPES)
            analysis_modules = [m for m in analysis_modules if m.dimension == dimension]

        ctx = self.collect(symbol, name, market, refresh_cache=refresh_cache, data_types=data_types)

        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_map = {executor.submit(m.analyze, ctx): m for m in analysis_modules}
            for future in as_completed(future_map):
                try:
                    results.append(future.result())
                except Exception as e:
                    mod = future_map[future]
                    logger.error(f"分析模块 {mod.dimension} 执行失败: {e}")
                    results.append(AnalysisResult(
                        dimension=mod.dimension, status="unavailable",
                        summary=f"分析模块异常: {e}", metrics={}))

        commentary = {}
        if self._llm_enabled:
            commentary = self._generate_commentary(symbol, name, results)

        return results, commentary

    def _generate_commentary(self, symbol: str, name: str,
                             results: list[AnalysisResult]) -> dict[str, str]:
        commentary = {}
        provider = self._config.get("llm.provider", "openai")
        llm = self._registry.get_llm_backend(provider)
        if llm is None:
            logger.warning(f"未找到 LLM 后端: provider={provider}")
            return commentary

        try:
            from jinja2 import Environment, FileSystemLoader
            template_dir = Path(__file__).parent.parent / "llm" / "prompt_templates"
            env = Environment(loader=FileSystemLoader(str(template_dir)))

            for result in results:
                template_name = f"{result.dimension}_{provider}.jinja2"
                try:
                    template = env.get_template(template_name)
                    prompt = template.render(name=name, symbol=symbol, **result.metrics)
                    commentary[result.dimension] = llm.generate(prompt)
                except Exception as e:
                    logger.warning(f"生成 {result.dimension} 解读失败: {e}")
                    commentary[result.dimension] = ""

            # 综合总结
            summary_template_name = f"summary_{provider}.jinja2"
            try:
                template = env.get_template(summary_template_name)
                prompt = template.render(name=name, symbol=symbol, commentary=commentary)
                commentary["summary"] = llm.generate(prompt)
            except Exception as e:
                logger.warning(f"生成综合总结失败: {e}")
                commentary["summary"] = ""
        except Exception as e:
            logger.error(f"LLM 解读生成过程失败: {e}")

        return commentary

    def _get_cached(self, symbol: str, data_type: str) -> list | None:
        date_key = date.today().isoformat()
        raw = self._cache.get(data_type, symbol, date_key)
        if raw is None:
            return None
        try:
            data_list = json.loads(raw)
            return self._deserialize_cache(data_type, symbol, data_list)
        except Exception:
            return None

    def _set_cache(self, symbol: str, data_type: str, data: list):
        date_key = date.today().isoformat()
        try:
            serialized = json.dumps(
                [item.model_dump(mode="json") for item in data],
                ensure_ascii=False, default=str,
            )
            ttl = self._config.get(f"data.cache_ttl.{TTL_KEY_MAP.get(data_type, 'daily')}", 86400)
            self._cache.put(data_type, symbol, date_key, serialized, ttl_seconds=ttl)
        except Exception as e:
            logger.warning(f"缓存 {data_type} 失败: {e}")

    def _deserialize_cache(self, data_type: str, symbol: str, data_list: list) -> list:
        from src.data.schemas import FinancialData, PriceData, ValuationData, IndustryData, NewsData
        cls_map = {
            "price": PriceData, "financial": FinancialData,
            "valuation": ValuationData, "industry": IndustryData, "news": NewsData,
        }
        cls = cls_map.get(data_type)
        if cls is None:
            return []
        return [cls(**item) for item in data_list]

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

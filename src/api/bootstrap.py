"""Agent 核心组装 — CLI chat 与 API 共用的依赖装配"""
import logging
from dataclasses import dataclass

from agent.pipeline_tools import (
    AnalyzeIndexTool,
    AnalyzeStockTool,
    GetSnapshotTool,
    ScreenStocksTool,
    _wrap_pipeline,
)
from agent.tools import ToolRegistry
from llm.base import LLMBackend

logger = logging.getLogger(__name__)


@dataclass
class AgentCore:
    """Agent 运行所需的核心依赖集合"""
    registry: ToolRegistry
    pipeline: object      # 真实 Pipeline（run(symbol, name, market) 接口）
    index_pipeline: object
    llm: LLMBackend | None = None


def build_llm(config) -> LLMBackend | None:
    """按配置构建 LLM 后端；未配置 api_key 或初始化失败返回 None"""
    provider = config.get("llm.provider", "openai")
    api_key = config.get("llm.api_key", "")
    base_url = config.get("llm.base_url", "") or None
    retry_times = config.get("llm.retry_times", 2)
    timeout = config.get("llm.timeout_seconds", 60)

    if not api_key:
        return None
    try:
        if provider == "openai":
            from llm.openai import OpenAIAdapter
            return OpenAIAdapter(
                api_key=api_key,
                model=config.get("llm.model", "gpt-4o"),
                temperature=config.get("llm.temperature", 0.3),
                max_tokens=config.get("llm.max_tokens", 2000),
                base_url=base_url,
                timeout=timeout,
                retry_times=retry_times,
            )
        elif provider == "claude":
            from llm.claude import ClaudeAdapter
            return ClaudeAdapter(
                api_key=api_key,
                model=config.get("llm.model", "claude-sonnet-4-6"),
                temperature=config.get("llm.temperature", 0.3),
                max_tokens=config.get("llm.max_tokens", 2000),
                base_url=base_url,
                timeout=timeout,
                retry_times=retry_times,
            )
    except Exception as e:  # noqa: BLE001 — LLM SDK 初始化失败降级为无 LLM
        logger.warning(f"LLM 后端初始化失败: {e}")
    return None


def build_agent_core(config=None, llm_enabled: bool | None = None) -> AgentCore:
    """组装 Agent 完整依赖：Pipeline、工具注册表、LLM

    Pipeline/IndexPipeline 各构建一次并注入工具，消除每次工具调用重建的开销。
    """
    from analysis.financial import FinancialAnalyzer
    from analysis.industry import IndustryAnalyzer
    from analysis.sentiment import SentimentAnalyzer
    from analysis.technical import TechnicalAnalyzer
    from analysis.valuation import ValuationAnalyzer
    from core.pipeline import Pipeline
    from core.registry import Registry
    from data.akshare import AkShareAdapter
    from index.pipeline import IndexPipeline
    from utils.config import Config

    config = config or Config()
    llm = build_llm(config)

    reg = Registry()
    reg.register_data_source(AkShareAdapter())
    reg.register_analysis_module(FinancialAnalyzer())
    reg.register_analysis_module(TechnicalAnalyzer())
    reg.register_analysis_module(ValuationAnalyzer())
    reg.register_analysis_module(IndustryAnalyzer())
    reg.register_analysis_module(SentimentAnalyzer())
    if llm is not None:
        reg.register_llm_backend(llm, provider=config.get("llm.provider", "openai"))

    if llm_enabled is None:
        llm_enabled = config.get("llm.enabled", True)
    pipeline = Pipeline(registry=reg, config=config,
                        llm_enabled=llm_enabled and llm is not None)
    index_pipeline = IndexPipeline()

    registry = ToolRegistry()
    registry.register(AnalyzeStockTool(pipeline=_wrap_pipeline(pipeline)))
    registry.register(AnalyzeIndexTool(index_pipeline=index_pipeline))
    registry.register(GetSnapshotTool(index_pipeline=index_pipeline))
    registry.register(ScreenStocksTool())

    # RAG 工具（ChromaDB 不可用则静默跳过，与 CLI 原行为一致）
    try:
        from agent.rag_tools import RAGListSourcesTool, RAGSearchTool
        from rag.engine import RAGEngine

        rag_engine = RAGEngine()
        registry.register(RAGSearchTool(engine=rag_engine))
        registry.register(RAGListSourcesTool(engine=rag_engine))
        logger.info("RAG 工具已注册 (embedding=%s)", rag_engine.embedding_name)
    except Exception as e:  # noqa: BLE001 — RAG 不可用时降级为无 RAG 工具
        logger.warning("RAG 工具不可用，跳过注册: %s", e)

    return AgentCore(registry=registry, pipeline=pipeline,
                     index_pipeline=index_pipeline, llm=llm)

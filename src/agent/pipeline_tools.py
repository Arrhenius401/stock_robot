"""Pipeline 工具包装 — 将存量 Pipeline/IndexPipeline 包装为 ToolProtocol

不修改存量代码，仅通过包装器暴露为 Agent 可调用的统一工具。
所有 _get_pipeline() 方法使用懒加载避免循环导入。
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent.tools import ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 内部适配器 — 将 Pipeline.run(symbol, name, market) 包装为 targets 接口
# ---------------------------------------------------------------------------

@dataclass
class _PipelineAdapterResult:
    """统一的管道执行结果，供 pipeline_tools 使用"""
    reports: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class _TargetsRunner(Protocol):
    """run(targets=[...]) 风格的管道接口 — 存量 Pipeline 适配器与 IndexPipeline 均满足"""
    def run(self, targets: list[Any]) -> Any: ...


class _SnapshotProvider(Protocol):
    """提供指数估值快照的管道接口 — IndexPipeline 满足"""
    def get_snapshot(self, symbol: str) -> dict | None: ...


def _wrap_pipeline(pipeline) -> _TargetsRunner:
    """将真实 Pipeline 包装为支持 run(targets=[...]) → _PipelineAdapterResult 的适配器

    真实 Pipeline.run() 签名为 (symbol, name, market) → (results, commentary, ctx)，
    此适配器将其转换为统一的 targets 接口，与 mock 注入的测试接口一致。
    """
    class _Adapter:
        def run(self, targets):
            result = _PipelineAdapterResult()
            for target in targets:
                try:
                    results, commentary, ctx = pipeline.run(
                        target.symbol, target.name, target.market
                    )
                    # 构建 overview
                    overview = {}
                    price_data = ctx.price_data or []
                    if price_data:
                        overview["latest_close"] = price_data[-1].close
                        if hasattr(price_data[-1], "change_pct"):
                            overview["change_pct"] = price_data[-1].change_pct

                    # 构建 comments
                    comments = []
                    bulk = commentary.get("bulk", "") if commentary else ""
                    if bulk:
                        comments.append(bulk)

                    # 构建 dimensions
                    dimensions = {}
                    for r in results:
                        dimensions[r.dimension] = {
                            "status": r.status,
                            "summary": r.summary,
                            "score": r.score,
                            "metrics": r.metrics,
                        }

                    report = type("_Report", (), {
                        "code": target.symbol,
                        "name": target.name,
                        "overview": overview,
                        "comments": comments,
                        "dimensions": dimensions,
                    })()
                    result.reports.append(report)
                except Exception as e:  # noqa: BLE001 — 单目标失败不影响批量结果
                    logger.error(f"分析 {target.symbol} 失败: {e}")
                    result.errors.append(f"{target.symbol}: {e}")
            return result

    return _Adapter()


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

class AnalyzeStockTool:
    """单股全维度分析工具"""
    name = "analyze_stock"
    description = (
        "对单只 A 股进行全面分析，返回财务、技术面、估值、行业、舆情五个维度的分析报告。"
        "适用于需要深入了解某只股票的完整画像时使用。"
        "参数: symbol(6位股票代码)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "A股代码，6位数字，如 000001 或 600519"},
        },
        "required": ["symbol"],
    }
    tags = ["pipeline", "stock", "analysis"]
    source = "pipeline"

    def __init__(self, pipeline: _TargetsRunner | None = None):
        self._pipeline = pipeline

    async def execute(self, **kwargs) -> ToolResult:
        symbol = str(kwargs.get("symbol", "")).strip()
        if not symbol:
            return ToolResult(status="error", error="股票代码不能为空",
                             metadata={"source": "pipeline"})

        from data.schemas import AnalysisTarget

        try:
            pipeline = self._pipeline or self._get_pipeline()
            target = AnalysisTarget(
                target_type="stock", symbol=symbol,
                name=symbol, market="a-shares",
            )
            pipe_result = pipeline.run(targets=[target])
            errors = pipe_result.errors
            reports = pipe_result.reports

            if errors:
                return ToolResult(status="error", error="; ".join(errors),
                                 metadata={"source": "pipeline", "symbol": symbol})
            if not reports:
                return ToolResult(status="error",
                                 error=f"未能生成 {symbol} 的分析报告",
                                 metadata={"source": "pipeline", "symbol": symbol})

            report = reports[0]
            return ToolResult(
                status="success",
                data={
                    "code": report.code,
                    "name": report.name,
                    "overview": report.overview,
                    "comments": getattr(report, "comments", []),
                    "dimensions": getattr(report, "dimensions", {}),
                },
                metadata={"source": "pipeline", "symbol": symbol},
            )
        except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
            logger.error(f"analyze_stock 执行失败: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "pipeline", "symbol": symbol})

    @staticmethod
    def _get_pipeline() -> _TargetsRunner:
        from analysis.financial import FinancialAnalyzer
        from analysis.industry import IndustryAnalyzer
        from analysis.sentiment import SentimentAnalyzer
        from analysis.technical import TechnicalAnalyzer
        from analysis.valuation import ValuationAnalyzer
        from core.pipeline import Pipeline
        from core.registry import Registry
        from data.akshare import AkShareAdapter
        from utils.config import Config

        config = Config()
        reg = Registry()
        reg.register_data_source(AkShareAdapter())
        reg.register_analysis_module(FinancialAnalyzer())
        reg.register_analysis_module(TechnicalAnalyzer())
        reg.register_analysis_module(ValuationAnalyzer())
        reg.register_analysis_module(IndustryAnalyzer())
        reg.register_analysis_module(SentimentAnalyzer())

        provider = config.get("llm.provider", "openai")
        api_key = config.get("llm.api_key", "")
        base_url = config.get("llm.base_url", "") or None
        llm_enabled = config.get("llm.enabled", True)

        if llm_enabled and api_key:
            if provider == "openai":
                from llm.openai import OpenAIAdapter
                reg.register_llm_backend(OpenAIAdapter(
                    api_key=api_key, model=config.get("llm.model", "gpt-4o"),
                    temperature=config.get("llm.temperature", 0.3),
                    max_tokens=config.get("llm.max_tokens", 2000),
                    base_url=base_url), provider="openai")
            elif provider == "claude":
                from llm.claude import ClaudeAdapter
                reg.register_llm_backend(ClaudeAdapter(
                    api_key=api_key, model=config.get("llm.model", "claude-sonnet-4-6"),
                    temperature=config.get("llm.temperature", 0.3),
                    max_tokens=config.get("llm.max_tokens", 2000),
                    base_url=base_url), provider="claude")

        pipeline = Pipeline(registry=reg, config=config,
                           llm_enabled=llm_enabled and bool(api_key))
        return _wrap_pipeline(pipeline)


class AnalyzeIndexTool:
    """指数分析工具"""
    name = "analyze_index"
    description = (
        "分析指数，返回技术面、估值、资金面、宏观、舆情五个维度的判断。"
        "适用于判断大盘走势、板块强弱、市场情绪。"
        "参数: symbol(指数代码，如 000300 沪深300)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "指数代码，如 000300（沪深300）、000905（中证500）"},
        },
        "required": ["symbol"],
    }
    tags = ["pipeline", "index", "analysis"]
    source = "pipeline"

    def __init__(self, index_pipeline: _TargetsRunner | None = None):
        self._pipeline = index_pipeline

    async def execute(self, **kwargs) -> ToolResult:
        symbol = str(kwargs.get("symbol", "")).strip()
        if not symbol:
            return ToolResult(status="error", error="指数代码不能为空",
                             metadata={"source": "pipeline"})

        from data.index_mapping import IndexMapping
        from data.schemas import AnalysisTarget

        try:
            mapping = IndexMapping()
            entry = mapping.lookup(symbol)
            target = AnalysisTarget(
                target_type="index", symbol=symbol,
                name=entry.name if entry else symbol,
                market="a-shares",
                index_style=entry.index_style if entry else "broad",
            )
            pipeline = self._pipeline or self._get_pipeline()
            result = pipeline.run(targets=[target])

            if result.errors:
                return ToolResult(status="error", error="; ".join(result.errors),
                                 metadata={"source": "pipeline", "symbol": symbol})
            if not result.reports:
                return ToolResult(status="error",
                                 error=f"未能生成指数 {symbol} 的分析报告",
                                 metadata={"source": "pipeline", "symbol": symbol})

            report = result.reports[0]
            return ToolResult(
                status="success",
                data={
                    "code": report.code,
                    "name": report.name,
                    "overview": report.overview,
                    "composite_comment": getattr(report, "composite_comment", ""),
                    "position_coeff": getattr(report, "position_coeff", None),
                },
                metadata={"source": "pipeline", "symbol": symbol},
            )
        except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
            logger.error(f"analyze_index 执行失败: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "pipeline", "symbol": symbol})

    @staticmethod
    def _get_pipeline() -> _TargetsRunner:
        from index.pipeline import IndexPipeline
        return IndexPipeline()


class GetSnapshotTool:
    """指数估值快照工具 — 轻量查询，不走完整管道"""
    name = "get_snapshot"
    description = (
        "快速获取指数当前估值快照（PE、PE分位数），不执行完整分析。"
        "适用于快速估值判断或个股联动。"
        "参数: symbol(指数代码)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "指数代码"},
        },
        "required": ["symbol"],
    }
    tags = ["pipeline", "index", "valuation", "quick"]
    source = "pipeline"

    def __init__(self, index_pipeline: _SnapshotProvider | None = None):
        self._pipeline = index_pipeline

    async def execute(self, **kwargs) -> ToolResult:
        symbol = str(kwargs.get("symbol", "")).strip()
        if not symbol:
            return ToolResult(status="error", error="指数代码不能为空",
                             metadata={"source": "pipeline"})

        try:
            pipeline = self._pipeline or self._get_pipeline()
            data = pipeline.get_snapshot(symbol)
            if data is None:
                return ToolResult(
                    status="error",
                    error=f"无法获取指数 {symbol} 的估值快照，可能该指数不支持估值查询",
                    metadata={"source": "pipeline", "symbol": symbol},
                )
            return ToolResult(status="success", data=data,
                             metadata={"source": "pipeline", "symbol": symbol})
        except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
            logger.error(f"get_snapshot 执行失败: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "pipeline", "symbol": symbol})

    @staticmethod
    def _get_pipeline() -> _SnapshotProvider:
        from index.pipeline import IndexPipeline
        return IndexPipeline()


class ScreenStocksTool:
    """股票筛选工具"""
    name = "screen_stocks"
    description = (
        "按行业筛选 A 股标的。参数: industry(行业名称，如 新能源、医药、银行)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "industry": {"type": "string", "description": "行业名称"},
        },
        "required": ["industry"],
    }
    tags = ["pipeline", "stock", "screening"]
    source = "pipeline"

    async def execute(self, **kwargs) -> ToolResult:
        industry = str(kwargs.get("industry", "")).strip()
        if not industry:
            return ToolResult(status="error", error="行业名称不能为空",
                             metadata={"source": "pipeline"})

        try:
            from data.industry_classifier import IndustryClassifier
            classifier = IndustryClassifier()

            # IndustryClassifier 仅提供 symbol→行业 单向查表，
            # 此处反向遍历 _mapping 按行业名称模糊匹配
            matching = []
            for entry in classifier._mapping.values():
                sw1 = entry.sw_level1 or ""
                sw2 = entry.sw_level2 or ""
                if industry in sw1 or industry in sw2:
                    matching.append({
                        "symbol": entry.symbol,
                        "sw_level1": sw1,
                        "sw_level2": sw2,
                        "style_category": entry.style_category,
                    })

            if not matching:
                return ToolResult(
                    status="error",
                    error=f"未找到行业「{industry}」对应的标的。请确认行业名称正确。",
                    metadata={"source": "pipeline", "industry": industry},
                )

            return ToolResult(
                status="success",
                data={"stocks": matching, "industry": industry, "count": len(matching)},
                metadata={"source": "pipeline", "industry": industry},
            )
        except FileNotFoundError:
            return ToolResult(
                status="error",
                error="行业映射数据文件未找到，无法完成筛选",
                metadata={"source": "pipeline", "industry": industry},
            )
        except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
            logger.error(f"screen_stocks 执行失败: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "pipeline"})

"""IndexPipeline — 指数分析管道编排"""
import logging
from dataclasses import dataclass, field
from core.registry import Registry
from data.schemas import AnalysisTarget, IndexAnalysisContext, AnalysisResult, IndexReport
from index.collector import IndexDataCollector
from index.enricher import IndexValuationEnricher
from index.build_single import IndexReportBuilder
from index.build_compare import IndexCompareReportBuilder, CompareTable

logger = logging.getLogger(__name__)


@dataclass
class IndexPipelineResult:
    reports: list[IndexReport] = field(default_factory=list)
    compare: CompareTable | None = None
    errors: list[str] = field(default_factory=list)


class IndexPipeline:
    def __init__(self, registry: Registry | None = None):
        if registry is None:
            registry = Registry()
        self._registry = registry
        self._collector = IndexDataCollector(registry)
        self._enricher = IndexValuationEnricher()
        self._report_builder = IndexReportBuilder()
        self._compare_builder = IndexCompareReportBuilder()
        self._analysis_modules = self._init_analysis_modules()

    def _init_analysis_modules(self) -> list:
        from index.analysis.technical import IndexTechnicalAnalyzer
        from index.analysis.valuation import IndexValuationAnalyzer
        from index.analysis.capital_flow import CapitalFlowAnalyzer
        from index.analysis.macro import MacroAnalyzer
        from index.analysis.sentiment import IndexSentimentAnalyzer

        return [
            IndexTechnicalAnalyzer(),
            IndexValuationAnalyzer(),
            CapitalFlowAnalyzer(),
            MacroAnalyzer(),
            IndexSentimentAnalyzer(),
        ]

    def run(self, targets: list[AnalysisTarget]) -> IndexPipelineResult:
        reports: list[IndexReport] = []
        contexts: list[IndexAnalysisContext] = []
        errors: list[str] = []

        for target in targets:
            try:
                ctx = self._collector.collect(target)

                # 运行所有分析模块
                results: list[AnalysisResult] = []
                for module in self._analysis_modules:
                    try:
                        result = module.analyze(ctx)
                        results.append(result)
                    except Exception as e:
                        logger.error(f"分析模块 {module.dimension} 失败: {e}")
                        results.append(AnalysisResult(
                            dimension=module.dimension, status="unavailable",
                            summary=f"分析模块异常: {e}", metrics={}
                        ))

                report = self._report_builder.build(ctx, results)
                reports.append(report)
                contexts.append(ctx)

            except Exception as e:
                logger.error(f"指数 {target.symbol} 分析失败: {e}")
                errors.append(f"{target.symbol}: {e}")

        compare = None
        if len(reports) >= 2:
            compare = self._compare_builder.build(contexts, reports)

        return IndexPipelineResult(reports=reports, compare=compare, errors=errors)

    def get_snapshot(self, symbol: str) -> dict | None:
        """轻量快照接口 — 仅供个股联动使用，不跑完整指数管道"""
        try:
            target = AnalysisTarget(
                target_type="index", symbol=symbol,
                name=symbol, market="a-shares", index_style="broad"
            )
            ctx = self._collector.collect(target)
            if ctx.valuation_data is None:
                return None
            val = ctx.valuation_data
            return {
                "symbol": symbol,
                "pe_ttm": val.pe_ttm,
                "pe_percentile": val.pe_percentile,
                "valuation_valid": val.valuation_valid,
            }
        except Exception as e:
            logger.warning(f"获取指数快照失败 {symbol}: {e}")
            return None

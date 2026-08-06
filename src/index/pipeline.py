"""IndexPipeline — 指数分析管道编排"""
import logging
from dataclasses import dataclass, field
from core.registry import Registry
from core.pipeline import ProgressCallback
from data.schemas import AnalysisTarget, IndexAnalysisContext, AnalysisResult, IndexReport
from index.collector import IndexDataCollector
from index.enricher import IndexValuationEnricher
from index.build_single import IndexReportBuilder
from index.build_compare import IndexCompareReportBuilder, CompareTable

logger = logging.getLogger(__name__)

INDEX_DIMENSION_LABELS = {
    "index_technical": "技术面分析",
    "index_valuation": "估值分析",
    "index_capital_flow": "资金面分析",
    "index_macro": "宏观分析",
    "index_sentiment": "舆情分析",
}

# dimension → 适用的 index_style
INDEX_DIMENSION_STYLES = {
    "index_technical": ("broad", "sector", "overseas"),
    "index_valuation": ("broad", "sector", "overseas"),
    "index_capital_flow": ("broad", "sector"),
    "index_macro": ("broad", "overseas"),
    "index_sentiment": ("broad", "sector", "overseas"),
}


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

    def run(self, targets: list[AnalysisTarget],
            on_progress: ProgressCallback = None) -> IndexPipelineResult:
        reports: list[IndexReport] = []
        contexts: list[IndexAnalysisContext] = []
        errors: list[str] = []

        for target in targets:
            try:
                ctx = self._collector.collect(target, on_progress=on_progress)

                # 按 index_style 过滤适用的分析模块
                applicable_modules = [
                    m for m in self._analysis_modules
                    if target.index_style in INDEX_DIMENSION_STYLES.get(m.dimension, ())
                ]
                if not applicable_modules:
                    logger.warning(f"指数 {target.symbol} 的 index_style={target.index_style} "
                                   "无适用分析模块")
                total = len(applicable_modules)

                results: list[AnalysisResult] = []
                for i, module in enumerate(applicable_modules):
                    try:
                        result = module.analyze(ctx)
                        results.append(result)
                    except Exception as e:
                        logger.error(f"分析模块 {module.dimension} 失败: {e}")
                        results.append(AnalysisResult(
                            dimension=module.dimension, status="unavailable",
                            summary=f"分析模块异常: {e}", metrics={}
                        ))
                    if on_progress:
                        on_progress("analyze", i + 1, total,
                                   INDEX_DIMENSION_LABELS.get(module.dimension, module.dimension))

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

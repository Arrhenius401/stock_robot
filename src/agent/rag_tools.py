"""RAG 工具 — rag_search 语义检索 + rag_list_sources 知识源清单"""
import logging

from agent.tools import ToolResult

logger = logging.getLogger(__name__)


class RAGSearchTool:
    name = "rag_search"
    description = (
        "在知识库中语义检索相关信息，返回最相关的文档片段。"
        "知识库包含：券商研报、财报公告、政策宏观、学术文献、历史分析报告、系统规则。"
        "参数: query(查询文本), source_type(可选，限定知识库类型), "
        "symbol(可选，限定股票代码), top_k(可选，返回数量，默认5)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "查询文本，支持自然语言"},
            "source_type": {
                "type": "string",
                "description": "知识库类型: research_reports/financial_filings/policy_macro/academic/history_reports/system_rules",
            },
            "symbol": {"type": "string", "description": "限定股票代码，如 000001"},
            "top_k": {"type": "integer", "description": "返回结果数量，默认 5"},
        },
        "required": ["query"],
    }
    tags = ["rag", "knowledge", "search"]
    source = "rag"

    def __init__(self, engine=None):
        self._engine = engine

    async def execute(self, **kwargs) -> ToolResult:
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return ToolResult(status="error", error="查询参数 query 不能为空",
                             metadata={"source": "rag"})

        source_type = kwargs.get("source_type", "")
        symbol = kwargs.get("symbol", "")
        top_k = int(kwargs.get("top_k", 5))

        filters = {}
        if symbol:
            filters["symbol"] = str(symbol).strip()
        if source_type:
            filters["source_type"] = str(source_type).strip()

        try:
            if source_type:
                collection_name = source_type
            else:
                collection_name = "research_reports"

            if self._engine is None:
                from rag.engine import RAGEngine
                engine = RAGEngine()
            else:
                engine = self._engine

            results = engine.search(
                collection_name=collection_name,
                query=query,
                top_k=top_k,
                filters=filters if filters else None,
            )

            return ToolResult(
                status="success",
                data={
                    "query": query,
                    "results": results,
                    "total": len(results),
                    "embedding_model": getattr(engine, "embedding_name", "unknown"),
                },
                metadata={"source": "rag", "collection": collection_name},
            )
        except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
            logger.error("rag_search 执行失败: %s", e)
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "rag"})


class RAGListSourcesTool:
    name = "rag_list_sources"
    description = (
        "列出当前已索引的所有知识源，包含文档标题、来源路径、关联股票代码、"
        "文档日期、标签和分块数量等信息。用于了解知识库的覆盖范围。"
    )
    parameters = {
        "type": "object",
        "properties": {},
    }
    tags = ["rag", "knowledge", "management"]
    source = "rag"

    def __init__(self, engine=None):
        self._engine = engine

    async def execute(self, **kwargs) -> ToolResult:
        try:
            if self._engine is None:
                from rag.engine import RAGEngine
                engine = RAGEngine()
            else:
                engine = self._engine

            sources = engine.list_sources()

            collection_summary = {}
            for s in sources:
                col = s["collection"]
                if col not in collection_summary:
                    collection_summary[col] = {"count": 0, "total_chunks": 0}
                collection_summary[col]["count"] += 1
                collection_summary[col]["total_chunks"] += s.get("chunks_count", 0)

            return ToolResult(
                status="success",
                data={
                    "sources": sources,
                    "total": len(sources),
                    "collection_summary": collection_summary,
                },
                metadata={"source": "rag"},
            )
        except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
            logger.error("rag_list_sources 执行失败: %s", e)
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "rag"})

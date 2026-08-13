"""RAG 工具单元测试"""
import pytest

from agent.rag_tools import RAGListSourcesTool, RAGSearchTool


class FakeRAGEngine:
    def __init__(self, search_results=None):
        self._search_results = search_results or []
        self._sources = [
            {
                "collection": "research_reports",
                "title": "策略周报",
                "source_path": "/data/weekly.md",
                "source_hash": "abc123",
                "date": "2026-01-15",
                "symbols": ["000001"],
                "tags": ["策略"],
                "chunks_count": 5,
            }
        ]
        self.last_search_collection = None
        self.last_search_query = None

    def search(self, collection_name, query, top_k=5, filters=None):
        self.last_search_collection = collection_name
        self.last_search_query = query
        return self._search_results

    def list_sources(self):
        return self._sources

    @property
    def embedding_name(self):
        return "bge-small-zh"


class TestRAGSearchTool:
    def test_tool_metadata(self):
        tool = RAGSearchTool(engine=FakeRAGEngine())
        assert tool.name == "rag_search"
        assert tool.source == "rag"
        assert "rag" in tool.tags
        assert "knowledge" in tool.tags

    def test_parameters_has_query_field(self):
        tool = RAGSearchTool(engine=FakeRAGEngine())
        params = tool.parameters
        assert params["type"] == "object"
        assert "query" in params["properties"]
        assert "query" in params["required"]

    @pytest.mark.asyncio
    async def test_execute_returns_success_with_results(self):
        engine = FakeRAGEngine(search_results=[
            {"content": "新能源行业前景看好", "metadata": {"title": "研报1"}, "score": 0.95},
        ])
        tool = RAGSearchTool(engine=engine)
        result = await tool.execute(query="新能源 前景")
        assert result.status == "success"
        assert len(result.data["results"]) == 1
        assert result.data["results"][0]["content"] == "新能源行业前景看好"

    @pytest.mark.asyncio
    async def test_execute_returns_empty_results_gracefully(self):
        engine = FakeRAGEngine(search_results=[])
        tool = RAGSearchTool(engine=engine)
        result = await tool.execute(query="不存在的查询")
        assert result.status == "success"
        assert result.data["results"] == []

    @pytest.mark.asyncio
    async def test_execute_with_source_type_filter(self):
        engine = FakeRAGEngine(search_results=[])
        tool = RAGSearchTool(engine=engine)
        result = await tool.execute(query="银行", source_type="research_reports")
        assert result.status == "success"
        assert engine.last_search_collection == "research_reports"

    @pytest.mark.asyncio
    async def test_execute_with_symbol_filter(self):
        engine = FakeRAGEngine(search_results=[])
        tool = RAGSearchTool(engine=engine)
        result = await tool.execute(query="财报", symbol="000001")
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_execute_catches_exceptions(self):
        class BrokenEngine:
            def search(self, **kwargs):
                raise RuntimeError("引擎故障")
        tool = RAGSearchTool(engine=BrokenEngine())
        result = await tool.execute(query="测试")
        assert result.status == "error"
        assert "引擎故障" in (result.error or "")

    @pytest.mark.asyncio
    async def test_execute_missing_query(self):
        tool = RAGSearchTool(engine=FakeRAGEngine())
        result = await tool.execute()
        assert result.status == "error"


class TestRAGListSourcesTool:
    def test_tool_metadata(self):
        tool = RAGListSourcesTool(engine=FakeRAGEngine())
        assert tool.name == "rag_list_sources"
        assert tool.source == "rag"
        assert len(tool.description) > 0

    @pytest.mark.asyncio
    async def test_execute_returns_sources_list(self):
        engine = FakeRAGEngine()
        tool = RAGListSourcesTool(engine=engine)
        result = await tool.execute()
        assert result.status == "success"
        assert len(result.data["sources"]) == 1
        assert result.data["sources"][0]["title"] == "策略周报"

    @pytest.mark.asyncio
    async def test_execute_catches_exceptions(self):
        class BrokenEngine:
            def list_sources(self):
                raise RuntimeError("无法获取源列表")
        tool = RAGListSourcesTool(engine=BrokenEngine())
        result = await tool.execute()
        assert result.status == "error"

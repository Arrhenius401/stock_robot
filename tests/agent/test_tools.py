"""ToolResult / ToolProtocol / ToolRegistry 单元测试"""
import pytest
from agent.tools import ToolResult, ToolProtocol


class TestToolResult:
    def test_success_result_has_status_data_and_metadata(self):
        result = ToolResult(status="success", data={"price": 10.5},
                           metadata={"execution_time_ms": 42})

        assert result.status == "success"
        assert result.data == {"price": 10.5}
        assert result.error is None
        assert result.metadata == {"execution_time_ms": 42}

    def test_error_result_has_status_error_and_null_data(self):
        result = ToolResult(status="error", error="连接超时",
                           metadata={"source": "akshare"})

        assert result.status == "error"
        assert result.data is None
        assert result.error == "连接超时"
        assert result.metadata == {"source": "akshare"}

    def test_result_defaults_data_and_error_to_none(self):
        result = ToolResult(status="partial")

        assert result.data is None
        assert result.error is None
        assert result.metadata is None

    def test_metadata_defaults_to_none(self):
        result = ToolResult(status="success")

        assert result.metadata is None


class TestToolProtocol:
    def test_tool_protocol_defines_required_attributes(self):
        """验证 ToolProtocol 的接口契约 —— 运行时通过 hasattr 检查"""
        required = ["name", "description", "parameters", "tags", "source", "execute"]

        class MyTool:
            name = "test_tool"
            description = "用于测试的工具"
            parameters = {"type": "object", "properties": {}}
            tags = ["test"]
            source = "pipeline"

            async def execute(self, **kwargs):
                return ToolResult(status="success")

        tool = MyTool()
        for attr in required:
            assert hasattr(tool, attr), f"缺少属性: {attr}"
        assert tool.name == "test_tool"
        assert tool.source == "pipeline"
        assert tool.tags == ["test"]


class FakeTool:
    def __init__(self, name, description, parameters=None, tags=None,
                 source="pipeline", return_data=None):
        self.name = name
        self.description = description
        self.parameters = parameters or {"type": "object", "properties": {}}
        self.tags = tags or []
        self.source = source
        self._return = return_data

    async def execute(self, **kwargs):
        return ToolResult(status="success", data=self._return)


class TestToolRegistry:
    @pytest.fixture
    def registry(self):
        from agent.tools import ToolRegistry
        return ToolRegistry()

    @pytest.fixture
    def sample_tools(self):
        return [
            FakeTool("analyze_stock", "分析单只股票的基本面和技术面，需要 stock_code 参数",
                     tags=["pipeline", "stock", "analysis"]),
            FakeTool("analyze_index", "分析指数，需要 index_code 参数",
                     tags=["pipeline", "index", "analysis"]),
            FakeTool("rag_search", "搜索知识库获取研报观点",
                     tags=["rag", "knowledge"]),
        ]

    def test_register_adds_tool_to_registry(self, registry):
        tool = FakeTool("test", "test tool")
        registry.register(tool)
        assert registry.get("test") is tool

    def test_register_replaces_existing_same_name(self, registry):
        tool1 = FakeTool("dup", "first")
        tool2 = FakeTool("dup", "second")
        registry.register(tool1)
        registry.register(tool2)
        assert registry.get("dup") is tool2

    def test_list_all_returns_all_registered_tools(self, registry, sample_tools):
        for t in sample_tools:
            registry.register(t)
        names = [t.name for t in registry.list_all()]
        assert names == ["analyze_stock", "analyze_index", "rag_search"]

    def test_get_returns_none_for_unknown_name(self, registry):
        assert registry.get("nonexistent") is None

    def test_match_filters_by_tags(self, registry, sample_tools):
        for t in sample_tools:
            registry.register(t)

        results = registry.match("分析", tags=["pipeline"])
        names = [t.name for t in results]
        assert "analyze_stock" in names
        assert "analyze_index" in names
        assert "rag_search" not in names

    def test_match_without_tags_returns_all_with_similar_description(self, registry, sample_tools):
        for t in sample_tools:
            registry.register(t)

        results = registry.match("搜索")
        names = [t.name for t in results]
        assert "rag_search" in names

    def test_match_returns_empty_for_no_match(self, registry, sample_tools):
        for t in sample_tools:
            registry.register(t)

        results = registry.match("翻译文档", tags=["mcp_external"])
        assert results == []

    def test_match_tag_index_is_built_on_register(self, registry):
        tool = FakeTool("multi_tag", "test", tags=["pipeline", "stock", "analysis"])
        registry.register(tool)

        assert "analyze_stock" not in [t.name for t in registry.match("whatever", tags=["rag"])]
        result = registry.match("股票", tags=["stock"])[0]
        assert result.name == "multi_tag"

    def test_register_duplicate_name_updates_tag_index(self, registry):
        tool1 = FakeTool("t1", "desc", tags=["pipeline"])
        tool2 = FakeTool("t1", "desc", tags=["rag"])
        registry.register(tool1)
        registry.register(tool2)

        assert len(registry.match("desc", tags=["pipeline"])) == 0
        assert len(registry.match("desc", tags=["rag"])) == 1

    def test_list_all_summary_returns_names_and_descriptions(self, registry, sample_tools):
        for t in sample_tools:
            registry.register(t)

        summary = registry.list_all_summary()
        assert len(summary) == 3
        for item in summary:
            assert "name" in item
            assert "description" in item
            assert "tags" in item

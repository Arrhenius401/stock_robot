"""MCPAdapter 单元测试"""
import pytest

from mcp.adapter import MCPAdapter
from mcp.schemas import MCPToolCallResult, MCPToolDefinition


class TestMCPAdapterToolToProtocol:
    def test_translate_creates_tool_protocol(self):
        mcp_tool = MCPToolDefinition(
            name="fetch_stock_data", description="获取股票行情数据",
            input_schema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
        )
        tool = MCPAdapter.tool_to_protocol(mcp_tool, source="mcp_internal")
        assert tool.name == "fetch_stock_data"
        assert tool.description == "获取股票行情数据"
        assert tool.source == "mcp_internal"
        assert tool.parameters == mcp_tool.input_schema
        assert hasattr(tool, "execute")

    def test_translate_external_source(self):
        mcp_tool = MCPToolDefinition(name="news_search", description="搜索财经新闻",
                                     input_schema={"type": "object", "properties": {}})
        tool = MCPAdapter.tool_to_protocol(mcp_tool, source="mcp_external")
        assert tool.source == "mcp_external"

    def test_translate_tags_based_on_source(self):
        mcp_tool = MCPToolDefinition(name="test_tool", description="测试",
                                     input_schema={"type": "object", "properties": {}})
        internal = MCPAdapter.tool_to_protocol(mcp_tool, source="mcp_internal")
        assert "mcp" in internal.tags
        external = MCPAdapter.tool_to_protocol(mcp_tool, source="mcp_external")
        assert "mcp" in external.tags

    @pytest.mark.asyncio
    async def test_mcp_tool_protocol_execute_success(self):
        mcp_tool = MCPToolDefinition(name="test_tool", description="测试",
                                     input_schema={"type": "object", "properties": {}})

        class FakeCaller:
            async def call_tool(self, name, arguments):
                return MCPToolCallResult(content=[{"type": "text", "text": "执行结果"}], is_error=False)

        adapter = MCPAdapter(caller=FakeCaller())
        tool = adapter.tool_to_protocol(mcp_tool, source="mcp_internal")
        result = await tool.execute(param1="value1")
        assert result.status == "success"
        assert "执行结果" in str(result.data)

    @pytest.mark.asyncio
    async def test_mcp_tool_protocol_execute_error(self):
        mcp_tool = MCPToolDefinition(name="broken_tool", description="故障工具",
                                     input_schema={"type": "object", "properties": {}})

        class FakeCaller:
            async def call_tool(self, name, arguments):
                return MCPToolCallResult(content=[{"type": "text", "text": "超时错误"}], is_error=True)

        adapter = MCPAdapter(caller=FakeCaller())
        tool = adapter.tool_to_protocol(mcp_tool, source="mcp_internal")
        result = await tool.execute()
        assert result.status == "error"


class TestMCPAdapterResultToMCP:
    def test_success_result_to_mcp(self):
        from agent.tools import ToolResult
        tr = ToolResult(status="success", data={"price": 10.5})
        mcp_result = MCPAdapter.result_to_mcp(tr)
        assert mcp_result.is_error is False
        assert "10.5" in mcp_result.content[0]["text"]

    def test_error_result_to_mcp(self):
        from agent.tools import ToolResult
        tr = ToolResult(status="error", error="连接超时")
        mcp_result = MCPAdapter.result_to_mcp(tr)
        assert mcp_result.is_error is True
        assert "连接超时" in mcp_result.content[0]["text"]

    def test_bulk_translate_tools(self):
        mcp_tools = [
            MCPToolDefinition(name="t1", description="d1", input_schema={}),
            MCPToolDefinition(name="t2", description="d2", input_schema={}),
        ]
        protocols = MCPAdapter.bulk_translate(mcp_tools, source="mcp_external")
        assert len(protocols) == 2
        assert protocols[0].name == "t1"
        assert protocols[1].name == "t2"

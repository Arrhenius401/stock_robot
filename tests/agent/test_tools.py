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

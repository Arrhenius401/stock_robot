"""MCP <-> ToolProtocol 适配器"""
import json
import logging
from typing import cast

from agent.tools import ToolResult
from mcp.schemas import MCPToolCallResult, MCPToolDefinition

logger = logging.getLogger(__name__)


class MCPAdapter:
    def __init__(self, caller=None):
        self._caller = caller

    def tool_to_protocol(self, mcp_tool: MCPToolDefinition | None = None,
                         source: str = "mcp_internal"):
        """支持两种调用方式（测试约定）：
        - MCPAdapter.tool_to_protocol(mcp_tool, source=...)   类方法风格（无 caller）
        - adapter.tool_to_protocol(mcp_tool, source=...)      实例方法风格（带 caller）
        """
        if mcp_tool is None:
            # 类方法风格：第一个位置参数实际是 mcp_tool，本方法无 caller 上下文
            mcp_tool = cast(MCPToolDefinition, self)
            adapter = None
        else:
            adapter = self
        # 注意：类体内不能直接引用同名的外层参数（类体赋值会使其变为类局部名），
        # 因此先复制到 src 再闭包捕获。
        src = source

        class _MCPToolProtocol:
            name = mcp_tool.name
            description = mcp_tool.description
            parameters = mcp_tool.input_schema
            tags = (["mcp", "internal"] if src == "mcp_internal" else ["mcp", "external"])
            source = src

            async def execute(self, **kwargs):
                # 闭包捕获的变量不做流收窄，复制到局部变量
                caller = adapter
                if caller is None or caller._caller is None:
                    return ToolResult(status="error", error="无 MCP 调用器上下文",
                                     metadata={"source": source})
                try:
                    mcp_result = await caller._caller.call_tool(name=mcp_tool.name, arguments=kwargs)
                    if mcp_result.is_error:
                        error_text = "".join(c.get("text", "") for c in mcp_result.content)
                        return ToolResult(status="error", error=error_text,
                                         metadata={"source": source, "mcp_tool": mcp_tool.name})
                    result_text = "".join(c.get("text", "") for c in mcp_result.content)
                    return ToolResult(status="success", data=result_text,
                                     metadata={"source": source, "mcp_tool": mcp_tool.name})
                except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
                    logger.error("MCP 工具 %s 执行失败: %s", mcp_tool.name, e)
                    return ToolResult(status="error", error=str(e),
                                     metadata={"source": source, "mcp_tool": mcp_tool.name})

        return _MCPToolProtocol()

    @staticmethod
    def result_to_mcp(result: ToolResult) -> MCPToolCallResult:
        text = json.dumps({"status": result.status, "data": result.data, "error": result.error},
                          ensure_ascii=False, default=str)
        return MCPToolCallResult(content=[{"type": "text", "text": text}],
                                is_error=(result.status == "error"))

    @staticmethod
    def bulk_translate(mcp_tools: list[MCPToolDefinition], source: str = "mcp_internal") -> list:
        class _StaticMCPTool:
            def __init__(self, mt, src):
                self.name = mt.name
                self.description = mt.description
                self.parameters = mt.input_schema
                self.tags = (["mcp", "internal"] if src == "mcp_internal" else ["mcp", "external"])
                self.source = src
            async def execute(self, **kwargs):
                return ToolResult(status="error", error="此工具需要 MCP Gateway 运行时支持",
                                 metadata={"source": self.source})
        return [_StaticMCPTool(t, source) for t in mcp_tools]

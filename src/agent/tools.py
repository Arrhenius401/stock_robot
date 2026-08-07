"""工具系统 — ToolProtocol 协议、ToolResult 结构、ToolRegistry 注册表"""
from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass
class ToolResult:
    """统一的工具执行结果

    所有工具 execute() 返回此结构，Executor 无需区分工具来源。
    工具内部异常捕获后转为 status="error"，不向上抛出。
    """
    status: Literal["success", "error", "partial"]
    data: Any = None
    error: str | None = None
    metadata: dict | None = None  # 含 execution_time_ms, source, tags 等


class ToolProtocol(Protocol):
    """所有工具实现此协议，Agent 不关心工具来源（pipeline/rag/mcp）"""
    name: str
    description: str       # LLM 阅读的语义描述，含使用场景和参数说明
    parameters: dict       # JSON Schema 格式的参数定义
    tags: list[str]        # 语义标签 ["pipeline", "stock", "screening"]
    source: Literal["pipeline", "rag", "mcp_internal", "mcp_external"]

    async def execute(self, **kwargs) -> ToolResult: ...

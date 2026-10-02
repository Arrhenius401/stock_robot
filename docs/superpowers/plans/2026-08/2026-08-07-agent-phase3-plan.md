# Agent 架构 Phase 3 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 MCP Gateway（内部 Server + 外部 Client 适配器）+ FastAPI HTTP API + Web UI 原型，交付多端可用的 Agent 系统。

**Architecture:** 新增 `src/mcp/` 模块（gateway/server/adapter），实现 MCP JSON-RPC 2.0 协议的最小兼容子集（`initialize`/`tools/list`/`tools/call`），内部 Server 通过 stdio 暴露本地工具，MCPAdapter 将 MCP 工具定义统一转为 `ToolProtocol`。新增 `src/api/` FastAPI 应用，复用 Agent 核心提供 REST + SSE 流式接口。Web UI 使用单页 HTML + 原生 fetch。

**Tech Stack:** Python 3.11+, fastapi>=0.115, uvicorn, pytest + pytest-asyncio + httpx

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `src/mcp/__init__.py` | 模块导出 |
| `src/mcp/schemas.py` | MCP JSON-RPC 消息类型、工具定义结构 |
| `src/mcp/adapter.py` | MCP 工具定义 ↔ ToolProtocol 双向适配 |
| `src/mcp/server.py` | 内部 MCP Server — stdio JSON-RPC，暴露本地工具 |
| `src/mcp/client.py` | 外部 MCP Client — subprocess 管理，工具发现 |
| `src/mcp/gateway.py` | MCP Gateway 入口 — 统一管理 Server/Client |
| `src/api/__init__.py` | API 模块导出 |
| `src/api/app.py` | FastAPI 应用 — chat/stream/tools/analyze/index 端点 |
| `src/api/static/index.html` | Web UI 原型 |
| `tests/mcp/__init__.py` | 测试包 |
| `tests/mcp/test_schemas.py` | JSON-RPC 消息单测 |
| `tests/mcp/test_adapter.py` | MCP ↔ ToolProtocol 适配器单测 |
| `tests/mcp/test_server.py` | 内部 MCP Server 单测 |
| `tests/mcp/test_client.py` | 外部 MCP Client 单测 |
| `tests/mcp/test_gateway.py` | MCP Gateway 集成测试 |
| `tests/api/__init__.py` | API 测试包 |
| `tests/api/test_app.py` | FastAPI 端点测试 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `pyproject.toml` | 新增依赖：fastapi, uvicorn |

### 不修改

- `src/agent/` — 零侵入，MCP 工具通过 ToolProtocol 注入
- `src/rag/` — 不变
- `src/core/` / `src/analysis/` / `src/data/` — 不变
- 所有现有测试保留

---

### Task 1: MCP 数据结构 — JSON-RPC 消息类型 + MCP 工具定义

**Files:**
- Create: `src/mcp/__init__.py`
- Create: `src/mcp/schemas.py`
- Create: `tests/mcp/__init__.py`
- Create: `tests/mcp/test_schemas.py`

- [ ] **Step 1: 创建包初始化文件**

```bash
mkdir -p src/mcp tests/mcp
```

`src/mcp/__init__.py`:
```python
"""MCP Gateway — 内部 Server + 外部 Client + ToolProtocol 适配"""
```

`tests/mcp/__init__.py`:
```python
```

- [ ] **Step 2: 编写 MCP 数据结构的失败测试**

`tests/mcp/test_schemas.py`:
```python
"""MCP JSON-RPC 消息类型与工具定义单元测试"""
import json
from mcp.schemas import (
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCError,
    MCPToolDefinition,
    MCPToolCallRequest,
    MCPToolCallResult,
    MCP_LIST_TOOLS_REQUEST,
)


class TestJSONRPCRequest:
    def test_parse_valid_request(self):
        raw = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
        })
        req = JSONRPCRequest.parse(raw)
        assert req.jsonrpc == "2.0"
        assert req.id == 1
        assert req.method == "tools/list"
        assert req.params is None

    def test_parse_request_with_params(self):
        raw = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "test_tool", "arguments": {"a": 1}},
        })
        req = JSONRPCRequest.parse(raw)
        assert req.params == {"name": "test_tool", "arguments": {"a": 1}}

    def test_to_string_roundtrip(self):
        """序列化后重解析应得到相同内容"""
        req = JSONRPCRequest(method="tools/list", id=42)
        raw = req.to_json()
        parsed = JSONRPCRequest.parse(raw)
        assert parsed.method == "tools/list"
        assert parsed.id == 42

    def test_is_notification_when_id_is_none(self):
        """id 为 None 表示通知，不需要响应"""
        req = JSONRPCRequest(method="notifications/initialized", id=None)
        assert req.is_notification is True

    def test_is_notification_false_with_id(self):
        req = JSONRPCRequest(method="tools/call", id=1)
        assert req.is_notification is False


class TestJSONRPCResponse:
    def test_serialize_success_response(self):
        resp = JSONRPCResponse.success(id=1, result={"tools": []})
        d = resp.to_dict()
        assert d["jsonrpc"] == "2.0"
        assert d["id"] == 1
        assert "result" in d
        assert "error" not in d

    def test_serialize_error_response(self):
        resp = JSONRPCResponse.error(id=2, code=-32600, message="Invalid Request")
        d = resp.to_dict()
        assert "error" in d
        assert d["error"]["code"] == -32600
        assert d["error"]["message"] == "Invalid Request"


class TestMCPToolDefinition:
    def test_from_dict_maps_fields(self):
        d = {
            "name": "fetch_stock_data",
            "description": "获取股票行情数据",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "股票代码"},
                },
                "required": ["symbol"],
            },
        }
        tool = MCPToolDefinition.from_dict(d)
        assert tool.name == "fetch_stock_data"
        assert tool.description == "获取股票行情数据"
        assert tool.input_schema == d["inputSchema"]

    def test_to_dict_roundtrip(self):
        original = MCPToolDefinition(
            name="calc_indicator",
            description="计算技术指标",
            input_schema={
                "type": "object",
                "properties": {"indicator": {"type": "string"}},
            },
        )
        d = original.to_dict()
        restored = MCPToolDefinition.from_dict(d)
        assert restored.name == original.name
        assert restored.input_schema == original.input_schema

    def test_list_tools_request_is_correct(self):
        """验证 tools/list 标准请求格式"""
        d = MCP_LIST_TOOLS_REQUEST
        assert d["method"] == "tools/list"
        assert d["jsonrpc"] == "2.0"


class TestMCPToolCallResult:
    def test_success_result(self):
        result = MCPToolCallResult(
            content=[{"type": "text", "text": "分析完成"}],
            is_error=False,
        )
        d = result.to_dict()
        assert d["content"][0]["text"] == "分析完成"
        assert d["isError"] is False

    def test_error_result(self):
        result = MCPToolCallResult(
            content=[{"type": "text", "text": "工具执行失败: 网络超时"}],
            is_error=True,
        )
        d = result.to_dict()
        assert d["isError"] is True
```

- [ ] **Step 3: 运行测试确认失败**

Run: `PYTHONPATH=src python -m pytest tests/mcp/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: 实现 MCP 数据结构**

`src/mcp/schemas.py`:
```python
"""MCP JSON-RPC 2.0 消息类型与工具定义"""
import json
from dataclasses import dataclass, field


# 标准 JSON-RPC 请求（tools/list）
MCP_LIST_TOOLS_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
}


@dataclass
class JSONRPCRequest:
    """JSON-RPC 2.0 请求"""
    method: str
    id: int | str | None = None
    params: dict | None = None
    jsonrpc: str = "2.0"

    @classmethod
    def parse(cls, raw: str) -> "JSONRPCRequest":
        d = json.loads(raw)
        return cls(
            jsonrpc=d.get("jsonrpc", "2.0"),
            id=d.get("id"),
            method=d["method"],
            params=d.get("params"),
        )

    def to_json(self) -> str:
        body = {"jsonrpc": self.jsonrpc, "method": self.method, "id": self.id}
        if self.params is not None:
            body["params"] = self.params
        return json.dumps(body, ensure_ascii=False)

    @property
    def is_notification(self) -> bool:
        return self.id is None


@dataclass
class JSONRPCResponse:
    """JSON-RPC 2.0 响应"""
    id: int | str | None
    result: dict | None = None
    error: dict | None = None
    jsonrpc: str = "2.0"

    @classmethod
    def success(cls, id, result: dict) -> "JSONRPCResponse":
        return cls(id=id, result=result)

    @classmethod
    def error(cls, id, code: int, message: str) -> "JSONRPCResponse":
        return cls(id=id, error={"code": code, "message": message})

    def to_dict(self) -> dict:
        body = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.result is not None:
            body["result"] = self.result
        if self.error is not None:
            body["error"] = self.error
        return body

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class MCPToolDefinition:
    """MCP 工具定义 — 对应 tools/list 返回的单个工具条目"""
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "MCPToolDefinition":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            input_schema=d.get("inputSchema", {}),
        )

    def to_dict(self) -> dict:
        result = {"name": self.name, "description": self.description}
        if self.input_schema:
            result["inputSchema"] = self.input_schema
        return result


@dataclass
class MCPToolCallRequest:
    """MCP 工具调用请求参数"""
    name: str
    arguments: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "MCPToolCallRequest":
        return cls(name=d["name"], arguments=d.get("arguments", {}))


@dataclass
class MCPToolCallResult:
    """MCP 工具调用结果"""
    content: list[dict]
    is_error: bool = False

    def to_dict(self) -> dict:
        return {"content": self.content, "isError": self.is_error}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `PYTHONPATH=src python -m pytest tests/mcp/test_schemas.py -v`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add src/mcp/__init__.py src/mcp/schemas.py tests/mcp/__init__.py tests/mcp/test_schemas.py
git commit -m "feat(MCP): 添加 JSON-RPC 消息类型与 MCP 工具定义数据结构"
```

---

### Task 2: MCP Tool Adapter — MCP 工具定义 ↔ ToolProtocol

**Files:**
- Create: `src/mcp/adapter.py`
- Create: `tests/mcp/test_adapter.py`

- [ ] **Step 1: 编写适配器的失败测试**

`tests/mcp/test_adapter.py`:
```python
"""MCPAdapter — MCP 工具定义 ↔ ToolProtocol 适配器单元测试"""
import pytest
from mcp.schemas import MCPToolDefinition, MCPToolCallResult
from mcp.adapter import MCPAdapter


class TestMCPAdapterToolToProtocol:
    def test_translate_creates_tool_protocol(self):
        mcp_tool = MCPToolDefinition(
            name="fetch_stock_data",
            description="获取股票行情数据",
            input_schema={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        )
        tool = MCPAdapter.tool_to_protocol(mcp_tool, source="mcp_internal")
        assert tool.name == "fetch_stock_data"
        assert tool.description == "获取股票行情数据"
        assert tool.source == "mcp_internal"
        assert tool.parameters == mcp_tool.input_schema
        assert hasattr(tool, "execute")

    def test_translate_external_source(self):
        mcp_tool = MCPToolDefinition(
            name="news_search",
            description="搜索财经新闻",
            input_schema={"type": "object", "properties": {}},
        )
        tool = MCPAdapter.tool_to_protocol(mcp_tool, source="mcp_external")
        assert tool.source == "mcp_external"

    def test_translate_tags_based_on_source(self):
        mcp_tool = MCPToolDefinition(
            name="test_tool",
            description="测试",
            input_schema={"type": "object", "properties": {}},
        )
        internal = MCPAdapter.tool_to_protocol(mcp_tool, source="mcp_internal")
        assert "mcp" in internal.tags
        external = MCPAdapter.tool_to_protocol(mcp_tool, source="mcp_external")
        assert "mcp" in external.tags

    @pytest.mark.asyncio
    async def test_mcp_tool_protocol_execute_success(self):
        """通过适配器创建的 tool 的 execute() 应返回 ToolResult"""
        mcp_tool = MCPToolDefinition(
            name="test_tool",
            description="测试",
            input_schema={"type": "object", "properties": {}},
        )

        class FakeCaller:
            async def call_tool(self, name, arguments):
                return MCPToolCallResult(
                    content=[{"type": "text", "text": "执行结果"}],
                    is_error=False,
                )

        adapter = MCPAdapter(caller=FakeCaller())
        tool = adapter.tool_to_protocol(mcp_tool, source="mcp_internal")
        from agent.tools import ToolResult
        result = await tool.execute(param1="value1")
        assert result.status == "success"
        assert "执行结果" in str(result.data)

    @pytest.mark.asyncio
    async def test_mcp_tool_protocol_execute_error(self):
        mcp_tool = MCPToolDefinition(
            name="broken_tool",
            description="故障工具",
            input_schema={"type": "object", "properties": {}},
        )

        class FakeCaller:
            async def call_tool(self, name, arguments):
                return MCPToolCallResult(
                    content=[{"type": "text", "text": "超时错误"}],
                    is_error=True,
                )

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
        assert len(mcp_result.content) > 0
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src python -m pytest tests/mcp/test_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 MCPAdapter**

`src/mcp/adapter.py`:
```python
"""MCP ↔ ToolProtocol 适配器"""
import json
import logging
from agent.tools import ToolResult
from mcp.schemas import MCPToolDefinition, MCPToolCallResult

logger = logging.getLogger(__name__)


class MCPAdapter:
    """MCP 工具定义 ↔ ToolProtocol 双向适配

    tool_to_protocol: MCP 工具定义 → ToolProtocol（Agent 可调用）
    result_to_mcp:   ToolResult → MCPToolCallResult（返回给 MCP 客户端）
    """

    def __init__(self, caller=None):
        """caller 需实现 async call_tool(name, arguments) -> MCPToolCallResult"""
        self._caller = caller

    # ------------------------------------------------------------------
    # MCP → ToolProtocol
    # ------------------------------------------------------------------

    def tool_to_protocol(
        self,
        mcp_tool: MCPToolDefinition,
        source: str = "mcp_internal",
    ):
        """将单个 MCP 工具定义转为 ToolProtocol 实例"""
        adapter = self

        class _MCPToolProtocol:
            name = mcp_tool.name
            description = mcp_tool.description
            parameters = mcp_tool.input_schema
            tags = (["mcp", "internal"] if source == "mcp_internal"
                    else ["mcp", "external"])
            source = source

            async def execute(self, **kwargs):
                try:
                    mcp_result = await adapter._caller.call_tool(
                        name=mcp_tool.name,
                        arguments=kwargs,
                    )
                    if mcp_result.is_error:
                        error_text = "".join(
                            c.get("text", "") for c in mcp_result.content
                        )
                        return ToolResult(
                            status="error",
                            error=error_text,
                            metadata={"source": source, "mcp_tool": mcp_tool.name},
                        )
                    result_text = "".join(
                        c.get("text", "") for c in mcp_result.content
                    )
                    return ToolResult(
                        status="success",
                        data=result_text,
                        metadata={"source": source, "mcp_tool": mcp_tool.name},
                    )
                except Exception as e:
                    logger.error("MCP 工具 %s 执行失败: %s", mcp_tool.name, e)
                    return ToolResult(
                        status="error",
                        error=str(e),
                        metadata={"source": source, "mcp_tool": mcp_tool.name},
                    )

        return _MCPToolProtocol()

    # ------------------------------------------------------------------
    # ToolResult → MCP
    # ------------------------------------------------------------------

    @staticmethod
    def result_to_mcp(result: ToolResult) -> MCPToolCallResult:
        """将 ToolResult 转为 MCP 工具调用结果"""
        text = json.dumps(
            {"status": result.status, "data": result.data, "error": result.error},
            ensure_ascii=False,
            default=str,
        )
        return MCPToolCallResult(
            content=[{"type": "text", "text": text}],
            is_error=(result.status == "error"),
        )

    # ------------------------------------------------------------------
    # 批量转换
    # ------------------------------------------------------------------

    @staticmethod
    def bulk_translate(
        mcp_tools: list[MCPToolDefinition],
        source: str = "mcp_internal",
    ) -> list:
        """批量转换，为每个工具创建不带 caller 的 Protocol 骨架"""
        class _StaticMCPTool:
            def __init__(self, mcp_tool, src):
                self.name = mcp_tool.name
                self.description = mcp_tool.description
                self.parameters = mcp_tool.input_schema
                self.tags = (["mcp", "internal"] if src == "mcp_internal"
                            else ["mcp", "external"])
                self.source = src

            async def execute(self, **kwargs):
                return ToolResult(
                    status="error",
                    error="此工具需要 MCP Gateway 运行时支持",
                    metadata={"source": self.source},
                )

        return [_StaticMCPTool(t, source) for t in mcp_tools]
```

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=src python -m pytest tests/mcp/test_adapter.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp/adapter.py tests/mcp/test_adapter.py
git commit -m "feat(MCP): 添加 MCPAdapter — MCP 工具定义 ↔ ToolProtocol 双向适配"
```

---

### Task 3: 内部 MCP Server — stdio JSON-RPC 暴露本地工具

**Files:**
- Create: `src/mcp/server.py`
- Create: `tests/mcp/test_server.py`

- [ ] **Step 1: 编写 MCP Server 的失败测试**

`tests/mcp/test_server.py`:
```python
"""内部 MCP Server 单元测试"""
import json
import pytest
from mcp.schemas import JSONRPCRequest, JSONRPCResponse
from mcp.server import InternalMCPServer


class FakeTool:
    """模拟一个本地工具"""
    name = "test_tool"
    description = "测试工具"
    parameters = {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    }
    tags = ["test"]
    source = "pipeline"

    async def execute(self, **kwargs):
        from agent.tools import ToolResult
        return ToolResult(status="success", data={"result": kwargs.get("x", 0) * 2})


class FakeBrokenTool:
    name = "broken"
    description = "故障工具"
    parameters = {"type": "object", "properties": {}}
    tags = ["test"]
    source = "pipeline"

    async def execute(self, **kwargs):
        raise RuntimeError("模拟崩溃")


class TestInternalMCPServer:
    @pytest.fixture
    def server(self):
        """创建注册了一个工具的 server"""
        srv = InternalMCPServer(name="stock-robot", version="0.1.0")
        srv.register_tool(FakeTool())
        return srv

    def test_handle_initialize(self, server):
        req = JSONRPCRequest(method="initialize", id=1, params={
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "test-client", "version": "1.0"},
        })
        resp = server.handle_request(req)
        assert resp.result is not None
        assert resp.result["serverInfo"]["name"] == "stock-robot"

    def test_handle_tools_list(self, server):
        req = JSONRPCRequest(method="tools/list", id=2)
        resp = server.handle_request(req)
        tools = resp.result["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "test_tool"

    def test_handle_tools_list_empty(self):
        srv = InternalMCPServer(name="empty", version="0.1.0")
        req = JSONRPCRequest(method="tools/list", id=1)
        resp = srv.handle_request(req)
        assert resp.result["tools"] == []

    @pytest.mark.asyncio
    async def test_handle_tools_call_success(self, server):
        req = JSONRPCRequest(method="tools/call", id=3, params={
            "name": "test_tool",
            "arguments": {"x": 5},
        })
        resp = await server.handle_request_async(req)
        assert resp.result is not None
        assert resp.result["isError"] is False
        text = resp.result["content"][0]["text"]
        data = json.loads(text)
        assert data["data"]["result"] == 10

    @pytest.mark.asyncio
    async def test_handle_tools_call_unknown_tool(self, server):
        req = JSONRPCRequest(method="tools/call", id=4, params={
            "name": "nonexistent",
            "arguments": {},
        })
        resp = await server.handle_request_async(req)
        assert resp.error is not None
        assert resp.error["code"] == -32602

    @pytest.mark.asyncio
    async def test_handle_tools_call_tool_crash(self):
        srv = InternalMCPServer(name="test", version="0.1.0")
        srv.register_tool(FakeBrokenTool())
        req = JSONRPCRequest(method="tools/call", id=5, params={
            "name": "broken",
            "arguments": {},
        })
        resp = await srv.handle_request_async(req)
        assert resp.result["isError"] is True

    def test_handle_unknown_method(self, server):
        req = JSONRPCRequest(method="unknown/method", id=6)
        resp = server.handle_request(req)
        assert resp.error is not None
        assert resp.error["code"] == -32601

    def test_list_tools_response_format(self, server):
        """验证 tools/list 返回符合 MCP 规范"""
        req = JSONRPCRequest(method="tools/list", id=1)
        resp = server.handle_request(req)
        for tool in resp.result["tools"]:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
```

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src python -m pytest tests/mcp/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 InternalMCPServer**

`src/mcp/server.py`:
```python
"""内部 MCP Server — 通过 stdio JSON-RPC 暴露本地工具给外部 MCP 客户端

协议: JSON-RPC 2.0 over stdin/stdout
支持方法: initialize, tools/list, tools/call
"""
import json
import logging
import sys
from mcp.schemas import (
    JSONRPCRequest,
    JSONRPCResponse,
    MCPToolCallResult,
    MCP_LIST_TOOLS_REQUEST,
)
from mcp.adapter import MCPAdapter
from agent.tools import ToolResult

logger = logging.getLogger(__name__)

ERROR_PARSE = -32700
ERROR_METHOD = -32601
ERROR_PARAMS = -32602
ERROR_INTERNAL = -32603


class InternalMCPServer:
    """内部 MCP Server — 将本地 ToolProtocol 工具通过 MCP 协议暴露"""

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, name: str, version: str):
        self._name = name
        self._version = version
        self._tools: dict[str, object] = {}
        self._adapter = MCPAdapter()
        self._initialized = False

    # ------------------------------------------------------------------
    # 工具注册
    # ------------------------------------------------------------------

    def register_tool(self, tool):
        """注册一个 ToolProtocol 工具到 MCP Server"""
        self._tools[tool.name] = tool

    def register_tools(self, tools: list):
        for t in tools:
            self.register_tool(t)

    # ------------------------------------------------------------------
    # 请求分发
    # ------------------------------------------------------------------

    def handle_request(self, req: JSONRPCRequest) -> JSONRPCResponse:
        """同步处理请求（initialize 和 tools/list）"""
        method = req.method

        if method == "initialize":
            return self._handle_initialize(req)
        elif method == "tools/list":
            return self._handle_tools_list(req)
        else:
            return JSONRPCResponse.error(
                req.id, ERROR_METHOD,
                f"未知方法: {method}",
            )

    async def handle_request_async(self, req: JSONRPCRequest) -> JSONRPCResponse:
        """异步处理请求（tools/call）"""
        if req.method == "tools/call":
            return await self._handle_tools_call(req)
        return self.handle_request(req)

    # ------------------------------------------------------------------
    # 方法实现
    # ------------------------------------------------------------------

    def _handle_initialize(self, req: JSONRPCRequest) -> JSONRPCResponse:
        self._initialized = True
        return JSONRPCResponse.success(req.id, {
            "protocolVersion": self.PROTOCOL_VERSION,
            "serverInfo": {"name": self._name, "version": self._version},
            "capabilities": {"tools": {}},
        })

    def _handle_tools_list(self, req: JSONRPCRequest) -> JSONRPCResponse:
        tools = []
        for tool in self._tools.values():
            tools.append({
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.parameters,
            })
        return JSONRPCResponse.success(req.id, {"tools": tools})

    async def _handle_tools_call(self, req: JSONRPCRequest) -> JSONRPCResponse:
        params = req.params or {}
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        tool = self._tools.get(tool_name)
        if tool is None:
            return JSONRPCResponse.error(
                req.id, ERROR_PARAMS,
                f"未知工具: {tool_name}",
            )

        try:
            result = await tool.execute(**arguments)
        except Exception as e:
            logger.error("工具 %s 执行异常: %s", tool_name, e)
            result = ToolResult(status="error", error=str(e))

        mcp_result = MCPAdapter.result_to_mcp(result)
        return JSONRPCResponse.success(req.id, mcp_result.to_dict())

    # ------------------------------------------------------------------
    # stdio 传输
    # ------------------------------------------------------------------

    def serve_stdio(self):
        """通过 stdin/stdout 运行 MCP Server（阻塞）

        从 stdin 逐行读取 JSON-RPC 请求，处理后写入 stdout。
        """
        logger.info("MCP Server %s v%s 启动 (stdio)", self._name, self._version)
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    req = JSONRPCRequest.parse(line)
                except json.JSONDecodeError as e:
                    resp = JSONRPCResponse.error(
                        None, ERROR_PARSE, f"JSON 解析失败: {e}"
                    )
                    sys.stdout.write(resp.to_json() + "\n")
                    sys.stdout.flush()
                    continue

                if req.method in ("tools/call",):
                    import asyncio
                    try:
                        loop = asyncio.get_event_loop()
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                    resp = loop.run_until_complete(self.handle_request_async(req))
                else:
                    resp = self.handle_request(req)

                if not req.is_notification:
                    sys.stdout.write(resp.to_json() + "\n")
                    sys.stdout.flush()
        except KeyboardInterrupt:
            pass
        finally:
            logger.info("MCP Server 已关闭")

    def serve_stdio_async(self):
        """异步版本的 stdio 服务（供测试和嵌入式使用）"""
        import asyncio

        async def _run():
            logger.info("MCP Server %s v%s 启动 (stdio async)", self._name, self._version)
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            transport, _ = await asyncio.get_event_loop().connect_read_pipe(
                lambda: protocol, sys.stdin
            )
            writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
                lambda: asyncio.streams.FlowControlMixin(loop=asyncio.get_event_loop()),
                sys.stdout,
            )
            writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, asyncio.get_event_loop())

            while True:
                try:
                    line = await reader.readline()
                    if not line:
                        break
                    line_text = line.decode("utf-8").strip()
                    if not line_text:
                        continue
                    req = JSONRPCRequest.parse(line_text)
                    resp = await self.handle_request_async(req)
                    if not req.is_notification:
                        writer.write(resp.to_json().encode("utf-8") + b"\n")
                        await writer.drain()
                except Exception as e:
                    logger.error("处理请求异常: %s", e)

        asyncio.run(_run())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=src python -m pytest tests/mcp/test_server.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp/server.py tests/mcp/test_server.py
git commit -m "feat(MCP): 添加 InternalMCPServer — stdio JSON-RPC 暴露本地工具"
```

---

### Task 4: 外部 MCP Client — subprocess 工具发现

**Files:**
- Create: `src/mcp/client.py`
- Create: `tests/mcp/test_client.py`

`tests/mcp/test_client.py`:
```python
"""外部 MCP Client 单元测试"""
import json
import pytest
from mcp.schemas import MCPToolDefinition
from mcp.client import ExternalMCPClient


class FakeProcess:
    """模拟 MCP Server 子进程的 stdin/stdout"""
    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or []
        self._response_idx = 0
        self.stdin_writes = []
        self.terminated = False

    @property
    def stdout(self):
        return self

    @property
    def stdin(self):
        return self

    def readline(self):
        if self._response_idx < len(self.responses):
            resp = self.responses[self._response_idx]
            self._response_idx += 1
            return resp.encode("utf-8") if isinstance(resp, str) else resp
        return b""

    def write(self, data):
        self.stdin_writes.append(
            data.decode("utf-8") if isinstance(data, bytes) else data
        )

    def flush(self):
        pass

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


class TestExternalMCPClient:
    @pytest.fixture
    def client(self):
        return ExternalMCPClient(command="python", args=["-m", "fake_mcp"])

    def test_send_request_writes_jsonrpc(self, client):
        proc = FakeProcess(responses=[
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}),
        ])
        client._process = proc
        resp = client._send_request_sync("tools/list")
        assert len(proc.stdin_writes) > 0
        written = proc.stdin_writes[0]
        assert "tools/list" in written

    def test_parse_tools_list_response(self, client):
        proc = FakeProcess(responses=[
            json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {"name": "t1", "description": "工具1",
                         "inputSchema": {"type": "object", "properties": {}}},
                        {"name": "t2", "description": "工具2",
                         "inputSchema": {"type": "object", "properties": {}}},
                    ]
                },
            }),
        ])
        client._process = proc
        tools = client.list_tools()
        assert len(tools) == 2
        assert isinstance(tools[0], MCPToolDefinition)
        assert tools[0].name == "t1"
        assert tools[1].name == "t2"

    def test_list_tools_empty(self, client):
        proc = FakeProcess(responses=[
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}),
        ])
        client._process = proc
        tools = client.list_tools()
        assert tools == []

    def test_disconnect_terminates_process(self, client):
        proc = FakeProcess()
        client._process = proc
        client.disconnect()
        assert proc.terminated is True

    def test_connect_raises_when_command_not_found(self):
        client = ExternalMCPClient(command="nonexistent_command_xyz")
        with pytest.raises(RuntimeError, match="无法启动"):
            client.connect()

    def test_is_connected(self, client):
        assert client.is_connected is False
        proc = FakeProcess()
        client._process = proc
        assert client.is_connected is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=src python -m pytest tests/mcp/test_client.py -v`
Expected: FAIL — `ModuleNotFoundError`

`src/mcp/client.py`:
```python
"""外部 MCP Client — 通过 subprocess stdio 连接第三方 MCP Server"""
import json
import logging
import subprocess
from mcp.schemas import MCPToolDefinition, MCP_LIST_TOOLS_REQUEST

logger = logging.getLogger(__name__)


class ExternalMCPClient:
    """外部 MCP Client — 管理一个 MCP Server 子进程连接

    通过 subprocess 启动 MCP Server，使用 stdin/stdout JSON-RPC 通信。
    """

    def __init__(self, command: str, args: list[str] | None = None,
                 env: dict | None = None):
        self._command = command
        self._args = args or []
        self._env = env
        self._process: subprocess.Popen | None = None
        self._request_id = 0

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self):
        """启动 MCP Server 子进程"""
        cmd = [self._command] + self._args
        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=self._env,
            )
        except FileNotFoundError:
            raise RuntimeError(f"无法启动 MCP Server: 命令不存在 — {' '.join(cmd)}")
        except Exception as e:
            raise RuntimeError(f"启动 MCP Server 失败: {e}")

        # 发送 initialize 请求
        init_req = json.dumps({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "stock-robot", "version": "0.1.0"},
            },
        }, ensure_ascii=False)
        self._process.stdin.write(init_req + "\n")
        self._process.stdin.flush()
        init_resp = self._process.stdout.readline()
        logger.debug("MCP Server 初始化响应: %s", init_resp.strip())

    def disconnect(self):
        """终止 MCP Server 子进程"""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
            finally:
                self._process = None

    @property
    def is_connected(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # ------------------------------------------------------------------
    # MCP 方法
    # ------------------------------------------------------------------

    def list_tools(self) -> list[MCPToolDefinition]:
        """从连接的 MCP Server 获取工具列表"""
        resp = self._send_request_sync("tools/list")
        tools_data = resp.get("result", {}).get("tools", [])
        return [MCPToolDefinition.from_dict(t) for t in tools_data]

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send_request_sync(self, method: str, params: dict | None = None) -> dict:
        """发送同步 JSON-RPC 请求并返回解析后的响应"""
        if not self._process or self._process.poll() is not None:
            raise RuntimeError("MCP Server 未连接或已退出")

        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params:
            req["params"] = params

        self._process.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self._process.stdin.flush()
        raw = self._process.stdout.readline()

        if not raw:
            raise RuntimeError(f"MCP Server 无响应 (method={method})")

        return json.loads(raw)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=src python -m pytest tests/mcp/test_client.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp/client.py tests/mcp/test_client.py
git commit -m "feat(MCP): 添加 ExternalMCPClient — subprocess stdio 连接外部 MCP Server"
```

---

### Task 5: MCP Gateway 入口 — 统一 Server/Client 管理

**Files:**
- Create: `src/mcp/gateway.py`
- Create: `tests/mcp/test_gateway.py`

`tests/mcp/test_gateway.py`:
```python
"""MCP Gateway 集成测试"""
import pytest
from mcp.gateway import MCPGateway


class TestMCPGateway:
    @pytest.fixture
    def gateway(self):
        return MCPGateway(server_name="test-gw", server_version="0.1.0")

    def test_initial_state(self, gateway):
        assert gateway.server_name == "test-gw"
        assert len(gateway.list_internal_tools()) == 0
        assert len(gateway.list_external_clients()) == 0

    def test_register_local_tools(self, gateway):
        class DummyTool:
            name = "dummy"
            description = "test"
            parameters = {"type": "object", "properties": {}}
            tags = ["test"]
            source = "pipeline"
            async def execute(self, **kwargs):
                from agent.tools import ToolResult
                return ToolResult(status="success", data="ok")

        gateway.register_local_tool(DummyTool())
        tools = gateway.list_internal_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "dummy"

    def test_add_external_client(self, gateway, mocker):
        mock_client = mocker.MagicMock()
        mock_client.is_connected = True
        gateway.add_external_client("news_service", mock_client)
        assert "news_service" in gateway.list_external_clients()

    def test_disconnect_all(self, gateway, mocker):
        c1 = mocker.MagicMock()
        c2 = mocker.MagicMock()
        gateway.add_external_client("s1", c1)
        gateway.add_external_client("s2", c2)
        gateway.disconnect_all()
        c1.disconnect.assert_called_once()
        c2.disconnect.assert_called_once()

    def test_get_server(self, gateway):
        server = gateway.get_server()
        assert server is not None
        assert server._name == "test-gw"
```

- [ ] **Step 2: 运行测试确认失败 → 实现 → 通过**

`src/mcp/gateway.py`:
```python
"""MCP Gateway 入口 — 统一管理内部 Server 与外部 Client"""
import logging
from mcp.server import InternalMCPServer
from mcp.client import ExternalMCPClient
from mcp.adapter import MCPAdapter

logger = logging.getLogger(__name__)


class MCPGateway:
    """MCP Gateway — 双角色入口

    - 内部 Server: 将本地 ToolProtocol 工具通过 MCP 暴露
    - 外部 Client: 连接第三方 MCP Server，发现其工具并适配为 ToolProtocol
    """

    def __init__(self, server_name: str = "stock-robot",
                 server_version: str = "0.1.0"):
        self._server = InternalMCPServer(name=server_name, version=server_version)
        self._clients: dict[str, ExternalMCPClient] = {}
        self._adapter = MCPAdapter()

    # ------------------------------------------------------------------
    # 内部 Server
    # ------------------------------------------------------------------

    def register_local_tool(self, tool):
        """向内部 MCP Server 注册本地工具"""
        self._server.register_tool(tool)

    def list_internal_tools(self) -> list[dict]:
        """列出内部 Server 已注册的工具定义"""
        from mcp.schemas import JSONRPCRequest
        resp = self._server._handle_tools_list(
            JSONRPCRequest(method="tools/list", id=0)
        )
        return resp.result.get("tools", [])

    def get_server(self) -> InternalMCPServer:
        return self._server

    # ------------------------------------------------------------------
    # 外部 Client
    # ------------------------------------------------------------------

    def connect_external(self, name: str, command: str,
                         args: list[str] | None = None) -> ExternalMCPClient:
        """连接一个外部 MCP Server"""
        client = ExternalMCPClient(command=command, args=args)
        client.connect()
        self._clients[name] = client
        logger.info("已连接外部 MCP Server: %s (%s)", name, command)
        return client

    def add_external_client(self, name: str, client):
        """注册一个已连接的外部 MCP Client"""
        self._clients[name] = client

    def list_external_clients(self) -> list[str]:
        return list(self._clients.keys())

    def get_external_client(self, name: str) -> ExternalMCPClient | None:
        return self._clients.get(name)

    def discover_external_tools(self, client_name: str):
        """发现并注册外部 MCP Server 的所有工具"""
        client = self._clients.get(client_name)
        if not client:
            raise ValueError(f"外部 MCP Client 不存在: {client_name}")

        mcp_tools = client.list_tools()
        protocols = MCPAdapter.bulk_translate(mcp_tools, source="mcp_external")
        for tool in protocols:
            self._server.register_tool(tool)
        logger.info("从 %s 发现 %d 个工具", client_name, len(protocols))
        return protocols

    def disconnect_all(self):
        """断开所有外部 MCP Client"""
        for name, client in list(self._clients.items()):
            try:
                client.disconnect()
            except Exception as e:
                logger.warning("断开 %s 失败: %s", name, e)
        self._clients.clear()

    # ------------------------------------------------------------------
    # stdio 模式
    # ------------------------------------------------------------------

    def serve_stdio(self):
        """以 stdio MCP Server 模式运行（供外部客户端连接）"""
        self._server.serve_stdio()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `PYTHONPATH=src python -m pytest tests/mcp/test_gateway.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/mcp/gateway.py tests/mcp/test_gateway.py
git commit -m "feat(MCP): 添加 MCPGateway 入口 — 统一内部 Server 与外部 Client 管理"
```

---

### Task 6: FastAPI HTTP API

**Files:**
- Create: `src/api/__init__.py`
- Create: `src/api/app.py`
- Create: `tests/api/__init__.py`
- Create: `tests/api/test_app.py`
- Modify: `pyproject.toml`（添加 fastapi + uvicorn 依赖）

- [ ] **Step 1: 安装依赖并更新 pyproject.toml**

```bash
pip install fastapi uvicorn httpx
```

在 `pyproject.toml` 的 `dependencies` 中添加:
```toml
    "fastapi>=0.115",
    "uvicorn>=0.30",
```

- [ ] **Step 2: 编写 HTTP API 的失败测试**

`tests/api/__init__.py`:
```python
```

`tests/api/test_app.py`:
```python
"""FastAPI HTTP API 端点测试"""
import pytest
from httpx import ASGITransport, AsyncClient
from api.app import create_app


@pytest.fixture
def app():
    """创建测试用 app（使用 mock 依赖）"""
    from agent.tools import ToolRegistry
    registry = ToolRegistry()

    class MockTool:
        name = "test_tool"
        description = "测试工具"
        parameters = {"type": "object", "properties": {}}
        tags = ["test"]
        source = "pipeline"
        async def execute(self, **kwargs):
            from agent.tools import ToolResult
            return ToolResult(status="success", data={"result": "ok"})

    registry.register(MockTool())
    return create_app(registry=registry)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestToolsEndpoint:
    @pytest.mark.asyncio
    async def test_list_tools(self, client):
        resp = await client.get("/api/v1/tools")
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "test_tool"

    @pytest.mark.asyncio
    async def test_tool_summary_format(self, client):
        resp = await client.get("/api/v1/tools")
        tool = resp.json()["tools"][0]
        assert "name" in tool
        assert "description" in tool
        assert "tags" in tool
        assert "source" in tool


class TestChatEndpoint:
    @pytest.mark.asyncio
    async def test_chat_accepts_request(self, client):
        resp = await client.post("/api/v1/chat", json={
            "message": "帮我分析平安银行",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data

    @pytest.mark.asyncio
    async def test_chat_empty_message_returns_400(self, client):
        resp = await client.post("/api/v1/chat", json={
            "message": "",
        })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_with_session_id(self, client):
        resp = await client.post("/api/v1/chat", json={
            "message": "你好",
        }, headers={"X-Session-Id": "sess-001"})
        assert resp.status_code == 200


class TestStreamEndpoint:
    @pytest.mark.asyncio
    async def test_stream_returns_sse(self, client):
        resp = await client.post("/api/v1/chat/stream", json={
            "message": "测试流式响应",
        })
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")


class TestLegacyEndpoints:
    @pytest.mark.asyncio
    async def test_analyze_endpoint_exists(self, client):
        resp = await client.post("/api/v1/analyze", json={
            "symbol": "000001",
        })
        assert resp.status_code in (200, 400, 422, 501)

    @pytest.mark.asyncio
    async def test_index_endpoint_exists(self, client):
        resp = await client.post("/api/v1/index", json={
            "symbol": "000300",
        })
        assert resp.status_code in (200, 400, 422, 501)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `PYTHONPATH=src python -m pytest tests/api/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: 实现 FastAPI 应用**

`src/api/__init__.py`:
```python
"""HTTP API 模块 — FastAPI 多端接口"""
```

`src/api/app.py`:
```python
"""FastAPI HTTP API — REST + SSE 流式接口"""
import json
import asyncio
import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


def create_app(registry=None, planner=None, executor=None, memory=None):
    """创建 FastAPI 应用（支持依赖注入，便于测试）"""
    app = FastAPI(
        title="Stock Robot API",
        version="0.1.0",
        description="AI 驱动的股票分析研报助手 HTTP API",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    # ------------------------------------------------------------------
    # 工具列表
    # ------------------------------------------------------------------

    @app.get("/api/v1/tools")
    async def list_tools():
        if registry is None:
            return JSONResponse({"tools": []})
        tools = registry.list_all_summary()
        return JSONResponse({"tools": tools, "total": len(tools)})

    # ------------------------------------------------------------------
    # Agent 对话
    # ------------------------------------------------------------------

    @app.post("/api/v1/chat")
    async def chat(request: Request):
        body = await request.json()
        message = body.get("message", "").strip()
        session_id = request.headers.get("X-Session-Id", "default")

        if not message:
            raise HTTPException(status_code=422, detail="message 不能为空")

        # 非 Agent 模式下返回提示
        if planner is None or executor is None:
            return JSONResponse({
                "response": f"[API 模式] 收到消息: {message}（Agent 核心未注入，"
                            f"启动时请配置 planner/executor 参数）",
                "session_id": session_id,
            })

        try:
            plan = await planner.plan(message)
            exec_result = await executor.execute(plan, session_id=session_id)
            return JSONResponse({
                "response": exec_result.get("summary", ""),
                "plan": {
                    "goal": plan.goal,
                    "steps": [
                        {"id": s.id, "description": s.description, "status": s.status.value}
                        for s in plan.steps
                    ],
                },
                "session_id": session_id,
            })
        except Exception as e:
            logger.error("Agent 对话失败: %s", e)
            return JSONResponse({
                "response": f"处理请求时出错: {e}",
                "session_id": session_id,
            }, status_code=500)

    # ------------------------------------------------------------------
    # Agent 对话（SSE 流式）
    # ------------------------------------------------------------------

    @app.post("/api/v1/chat/stream")
    async def chat_stream(request: Request):
        body = await request.json()
        message = body.get("message", "").strip()
        session_id = request.headers.get("X-Session-Id", "default")

        if not message:
            raise HTTPException(status_code=422, detail="message 不能为空")

        async def event_stream():
            yield f"data: {json.dumps({'type': 'start', 'message': message})}\n\n"

            if planner is None or executor is None:
                yield f"data: {json.dumps({'type': 'text', 'content': '[API 模式] Agent 核心未注入'})}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            try:
                plan = await planner.plan(message)
                yield f"data: {json.dumps({'type': 'plan', 'goal': plan.goal, 'steps': [s.description for s in plan.steps]})}\n\n"

                exec_result = await executor.execute(plan, session_id=session_id)

                yield f"data: {json.dumps({'type': 'result', 'summary': exec_result.get('summary', '')})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    # ------------------------------------------------------------------
    # 存量分析能力开放
    # ------------------------------------------------------------------

    @app.post("/api/v1/analyze")
    async def analyze(request: Request):
        body = await request.json()
        symbol = body.get("symbol", "").strip()
        if not symbol:
            raise HTTPException(status_code=422, detail="symbol 不能为空")
        return JSONResponse({
            "status": "not_implemented",
            "message": "analyze 端点将在后续版本中实现完整的 Pipeline 调用",
            "symbol": symbol,
        }, status_code=501)

    @app.post("/api/v1/index")
    async def index(request: Request):
        body = await request.json()
        symbol = body.get("symbol", "").strip()
        if not symbol:
            raise HTTPException(status_code=422, detail="symbol 不能为空")
        return JSONResponse({
            "status": "not_implemented",
            "message": "index 端点将在后续版本中实现完整的 IndexPipeline 调用",
            "symbol": symbol,
        }, status_code=501)

    return app


# 默认 app 实例（供 uvicorn 直接使用）
app = create_app()
```

- [ ] **Step 5: 运行测试确认通过**

Run: `PYTHONPATH=src python -m pytest tests/api/test_app.py -v`
Expected: 9 passed

- [ ] **Step 6: Commit**

```bash
git add src/api/__init__.py src/api/app.py tests/api/__init__.py tests/api/test_app.py pyproject.toml
git commit -m "feat(API): 添加 FastAPI HTTP API — chat/stream/tools/analyze/index 端点"
```

---

### Task 7: Web UI 原型

**Files:**
- Create: `src/api/static/index.html`

- [ ] **Step 1: 创建 Web UI 静态页面**

```bash
mkdir -p src/api/static
```

`src/api/static/index.html`:
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Robot — AI 股票分析助手</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #1a1a2e; color: #e0e0e0; height: 100vh; display: flex; flex-direction: column; }
header { background: #16213e; padding: 12px 20px; border-bottom: 1px solid #0f3460; display: flex;
         align-items: center; justify-content: space-between; }
header h1 { font-size: 18px; color: #e94560; }
header span { font-size: 12px; color: #888; }
main { flex: 1; overflow-y: auto; padding: 20px; max-width: 900px; margin: 0 auto; width: 100%; }
.message { margin-bottom: 16px; padding: 12px 16px; border-radius: 8px; line-height: 1.6; }
.message.user { background: #0f3460; margin-left: 40px; }
.message.agent { background: #16213e; margin-right: 40px; border: 1px solid #333; }
.message.error { background: #3a0a0a; color: #ff6b6b; }
.message .role { font-size: 12px; color: #e94560; margin-bottom: 4px; font-weight: bold; }
.message pre { background: #0d1117; padding: 8px; border-radius: 4px; overflow-x: auto;
               margin: 8px 0; font-size: 13px; }
footer { background: #16213e; padding: 12px 20px; border-top: 1px solid #0f3460; display: flex; gap: 8px; }
footer input { flex: 1; padding: 10px 16px; border-radius: 6px; border: 1px solid #333;
               background: #1a1a2e; color: #e0e0e0; font-size: 14px; }
footer input:focus { outline: none; border-color: #e94560; }
footer button { padding: 10px 20px; border-radius: 6px; background: #e94560; color: white;
                border: none; cursor: pointer; font-size: 14px; font-weight: bold; }
footer button:hover { background: #c73a52; }
footer button:disabled { background: #555; cursor: not-allowed; }
.tools-bar { display: flex; gap: 8px; padding: 8px 20px; background: #16213e; border-top: 1px solid #0f3460;
             flex-wrap: wrap; }
.tools-bar button { padding: 4px 12px; border-radius: 4px; background: #0f3460; color: #aaa; border: 1px solid #333;
                    cursor: pointer; font-size: 12px; }
.tools-bar button:hover { background: #1a4a80; color: white; }
.spinner { display: inline-block; width: 12px; height: 12px; border: 2px solid #888;
           border-top-color: #e94560; border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<header>
  <h1>Stock Robot</h1>
  <span>AI 驱动的股票分析研报助手</span>
</header>
<main id="chat"></main>
<div class="tools-bar">
  <button onclick="sendQuick('大盘现在适合入场吗？')">择时研判</button>
  <button onclick="sendQuick('帮我列出可用工具')">工具列表</button>
  <button onclick="sendQuick('/clear')">清空会话</button>
</div>
<footer>
  <input id="input" type="text" placeholder="输入你的投资研究问题..."
         onkeydown="if(event.key==='Enter')sendMessage()">
  <button id="sendBtn" onclick="sendMessage()">发送</button>
</footer>

<script>
const chat = document.getElementById('chat');
const input = document.getElementById('input');
const sendBtn = document.getElementById('sendBtn');

function addMessage(role, text, isError) {
  const div = document.createElement('div');
  div.className = 'message ' + role + (isError ? ' error' : '');
  div.innerHTML = '<div class="role">' + (role === 'user' ? '你' : 'Stock Robot') + '</div>'
    + '<div class="content">' + text.replace(/\n/g, '<br>') + '</div>';
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

async function sendMessage() {
  const msg = input.value.trim();
  if (!msg) return;
  addMessage('user', msg);
  input.value = '';
  sendBtn.disabled = true;
  if (msg.startsWith('/')) { addMessage('agent', '快捷命令已发送: ' + msg); sendBtn.disabled = false; return; }
  try {
    const resp = await fetch('/api/v1/chat', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: msg})
    });
    const data = await resp.json();
    addMessage('agent', data.response || JSON.stringify(data), resp.status >= 400);
  } catch(e) {
    addMessage('agent', '请求失败: ' + e.message, true);
  }
  sendBtn.disabled = false;
  input.focus();
}

function sendQuick(msg) {
  input.value = msg;
  sendMessage();
}

input.focus();
</script>
</body>
</html>
```

- [ ] **Step 2: 修改 app.py 挂载静态文件**

在 `src/api/app.py` 的 `create_app` 函数中，在 `app = FastAPI(...)` 之后添加:

```python
from fastapi.staticfiles import StaticFiles
import os

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
```

- [ ] **Step 3: 验证 Uvicorn 启动**

Run: `cd d:/code/stock_robot && PYTHONPATH=src python -m uvicorn api.app:app --host 127.0.0.1 --port 8000 &`
Then: `curl http://127.0.0.1:8000/health`
Expected: `{"status":"ok","version":"0.1.0"}`

Stop the server after testing.

- [ ] **Step 4: Commit**

```bash
git add src/api/static/index.html src/api/app.py
git commit -m "feat(API): 添加 Web UI 原型"
```

---

### Task 8: 集成验证 — 全量测试 + 端到端检查

**Files:**
- 无新建文件

- [ ] **Step 1: 运行 MCP 全部测试**

Run: `PYTHONPATH=src python -m pytest tests/mcp/ -v`
Expected: all pass (~37 tests)

- [ ] **Step 2: 运行 API 全部测试**

Run: `PYTHONPATH=src python -m pytest tests/api/ -v`
Expected: all pass (~9 tests)

- [ ] **Step 3: 运行 Phase 2 RAG 测试**

Run: `PYTHONPATH=src python -m pytest tests/rag/ -v`
Expected: all pass (~89 tests)

- [ ] **Step 4: 运行 Phase 1 Agent 测试**

Run: `PYTHONPATH=src python -m pytest tests/agent/ -v`
Expected: all pass (~87 tests)

- [ ] **Step 5: 运行存量回归**

Run: `PYTHONPATH=src python -m pytest tests/ -v --ignore=tests/mcp --ignore=tests/api --ignore=tests/rag --ignore=tests/agent -x`
Expected: all pass (~375+ tests)

- [ ] **Step 6: 全量测试**

Run: `PYTHONPATH=src python -m pytest tests/ -v`
Expected: all pass (~590+ tests)

- [ ] **Step 7: Commit（如有修改）**

---

## 验证清单

- [ ] `pytest tests/mcp/ -v` — MCP 全部测试通过
- [ ] `pytest tests/api/ -v` — API 全部测试通过
- [ ] `pytest tests/rag/ -v` — RAG 测试通过（无回归）
- [ ] `pytest tests/agent/ -v` — Agent 测试通过（无回归）
- [ ] `pytest tests/ -v --ignore=tests/mcp --ignore=tests/api --ignore=tests/rag --ignore=tests/agent` — 存量通过
- [ ] `curl http://127.0.0.1:8000/health` — HTTP API 正常响应
- [ ] 浏览器打开 `http://127.0.0.1:8000/` — Web UI 正常加载

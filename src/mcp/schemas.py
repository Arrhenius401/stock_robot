"""MCP JSON-RPC 2.0 消息类型与工具定义"""
import json
from dataclasses import dataclass, field
from typing import Any

MCP_LIST_TOOLS_REQUEST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


@dataclass
class JSONRPCRequest:
    method: str
    id: int | str | None = None
    params: dict | None = None
    jsonrpc: str = "2.0"

    @classmethod
    def parse(cls, raw: str) -> "JSONRPCRequest":
        d = json.loads(raw)
        return cls(jsonrpc=d.get("jsonrpc", "2.0"), id=d.get("id"),
                   method=d["method"], params=d.get("params"))

    def to_json(self) -> str:
        body = {"jsonrpc": self.jsonrpc, "method": self.method, "id": self.id}
        if self.params is not None:
            body["params"] = self.params
        return json.dumps(body, ensure_ascii=False)

    @property
    def is_notification(self) -> bool:
        return self.id is None


class JSONRPCResponse:
    """JSON-RPC 2.0 响应

    注意：不能使用 @dataclass —— 字段 error 与同名 classmethod error() 冲突时，
    dataclass 会把类属性（classmethod）当作字段默认值，导致 success() 创建的
    实例 error 为 bound method 而非 None。因此手写 __init__，签名保持一致。
    """

    def __init__(self, id: int | str | None, result: dict | None = None,
                 error: dict | None = None, jsonrpc: str = "2.0"):
        self.id = id
        self.result = result
        # 实例属性与同名 classmethod error() 冲突
        self.error = error  # pyright: ignore[reportAttributeAccessIssue]
        self.jsonrpc = jsonrpc

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
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "MCPToolDefinition":
        return cls(name=d["name"], description=d.get("description", ""),
                   input_schema=d.get("inputSchema", {}))

    def to_dict(self) -> dict:
        result: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.input_schema: result["inputSchema"] = self.input_schema
        return result


@dataclass
class MCPToolCallResult:
    content: list[dict]
    is_error: bool = False

    def to_dict(self) -> dict:
        return {"content": self.content, "isError": self.is_error}

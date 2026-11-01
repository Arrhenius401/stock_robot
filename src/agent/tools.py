"""工具系统 — ToolProtocol 协议、ToolResult 结构、ToolRegistry 注册表"""
import ast
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

logger = logging.getLogger(__name__)


def model_tool_data(data: Any) -> Any:
    """只生成模型视图；原始工具结果保留给用户报告与成果归档。"""
    if isinstance(data, dict):
        return {key: model_tool_data(value) for key, value in data.items()
                if not (isinstance(value, dict) and value.get("reference_only") is True)}
    if isinstance(data, list):
        return [model_tool_data(value) for value in data
                if not (isinstance(value, dict) and value.get("reference_only") is True)]
    return data


def model_tool_message(content: str) -> str:
    """兼容已持久化的 JSON/Python 字典工具消息，原始消息不被改写。"""
    if "reference_only" not in content:
        return content
    match = re.match(r"^(\[[^\]]+\]\s+\w+:\s*)", content)
    prefix = match[0] if match else ""
    body = content[len(prefix):]
    try:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = ast.literal_eval(body)
        return prefix + json.dumps(model_tool_data(data), ensure_ascii=False, default=str)
    except (ValueError, SyntaxError, TypeError, RecursionError):
        logger.debug("含历史参考的旧工具消息无法完整解析，模型上下文省略该条原始内容")
        return prefix + "工具历史含仅供用户查看的参考，原始内容已从模型上下文省略"


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
    source: str            # pipeline / rag / mcp_internal / mcp_external

    async def execute(self, **kwargs) -> ToolResult: ...


class ToolRegistry:
    """工具注册表 —— 仅做工具映射，业务实现全部下沉引擎层

    匹配流程: 步骤描述 → tags 标签粗筛 → 关键词子串匹配排序 → 返回候选列表
    最终工具选定由 Executor 通过 LLM 二次校验完成。
    """

    def __init__(self):
        self._tools: dict[str, ToolProtocol] = {}
        self._index: dict[str, set[str]] = {}  # tag → set of tool names

    def register(self, tool: ToolProtocol) -> None:
        old = self._tools.get(tool.name)
        if old:
            for tag in old.tags:
                if tag in self._index:
                    self._index[tag].discard(tool.name)
        self._tools[tool.name] = tool
        for tag in tool.tags:
            if tag not in self._index:
                self._index[tag] = set()
            self._index[tag].add(tool.name)

    def get(self, name: str) -> ToolProtocol | None:
        return self._tools.get(name)

    def list_all(self) -> list[ToolProtocol]:
        return list(self._tools.values())

    def list_all_summary(self) -> list[dict]:
        """供 Planner system prompt 使用的工具摘要列表"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "tags": t.tags,
                "source": t.source,
            }
            for t in self._tools.values()
        ]

    def match(self, description: str, tags: list[str] | None = None) -> list[ToolProtocol]:
        """按描述子串匹配 + 可选标签过滤，返回相关性排序的候选列表"""
        candidates = set(self._tools.keys())

        if tags:
            for tag in tags:
                if tag in self._index:
                    candidates &= self._index[tag]
                else:
                    return []

        if not candidates:
            return []

        desc_lower = description.lower()
        scored = []
        for name in candidates:
            tool = self._tools[name]
            combined = f"{tool.name} {tool.description}".lower()
            score = 0
            for word in desc_lower.split():
                if word in combined:
                    score += 1
            if score > 0 or tags:
                scored.append((score, tool))

        if not scored and not tags:
            # CJK 无空格输入回退：按字符二元组匹配工具名/描述
            bigrams = {desc_lower[i:i + 2] for i in range(len(desc_lower) - 1)}
            for name in candidates:
                tool = self._tools[name]
                combined = f"{tool.name} {tool.description}".lower()
                score = sum(1 for bg in bigrams if bg in combined)
                if score > 0:
                    scored.append((score, tool))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [tool for _, tool in scored]

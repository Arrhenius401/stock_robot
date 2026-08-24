"""自主循环执行器 — create_react_agent 封装

模型在对话循环中自主决定工具调用时机（可零次或多次），工具结果回注，
直到模型认为任务完成。与 plan 模式的差异：不预拆步骤、不强制每步选工具。
"""
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError  # noqa: F401  # 重新导出供调用方捕获
from langgraph.prebuilt import create_react_agent

from agent.memory import Memory
from agent.tools import ToolProtocol, ToolRegistry

logger = logging.getLogger(__name__)

_JSON_TYPE_MAP = {"string": str, "integer": int, "number": float,
                  "boolean": bool, "array": list}

_HISTORY_WINDOW = 10
_SUMMARY_LIMIT = 500

EventCallback = Callable[[dict], None]


@dataclass
class AgentOutcome:
    """agent 模式执行结果：最终回答 + 工具调用统计"""
    final_reply: str
    tool_calls: list[dict] = field(default_factory=list)
    # tool_calls 元素: {"tool", "args", "status": "success|error", "summary"}


def _schema_to_model(name: str, parameters: dict) -> type[Any]:
    """ToolProtocol 的 JSON Schema parameters → pydantic 模型（args_schema 要求）"""
    from pydantic import BaseModel, Field, create_model

    properties = parameters.get("properties", {})
    required = set(parameters.get("required", []))
    fields: dict[str, Any] = {}
    for pname, prop in properties.items():
        ptype = _JSON_TYPE_MAP.get(prop.get("type", "string"), str)
        kwargs: dict[str, Any] = {"description": prop.get("description", "")}
        if pname not in required:
            kwargs["default"] = None
        fields[pname] = (ptype, Field(**kwargs))
    return create_model(name, __base__=BaseModel, **fields)


def _as_lc_tool(t: ToolProtocol) -> Any:
    """ToolProtocol → LangChain tool

    过滤 None 参数：schema 里 optional 字段默认 None，直接传入会让
    RAGSearchTool 等工具 int(None) 崩溃。
    langchain-core 1.5.x 的 tool() 装饰器不再接受 name 关键字（首参为
    name_or_callable），统一用 StructuredTool.from_function 显式构造。
    """
    async def _run(**kwargs):
        try:
            result = await t.execute(
                **{k: v for k, v in kwargs.items() if v is not None})
        except Exception as e:  # noqa: BLE001 — 工具异常隔离，转为错误文本回注模型
            logger.error("工具 %s 执行异常: %s", t.name, e)
            return f"错误: {e}"
        if result.status == "error":
            return f"错误: {result.error}"
        try:
            return json.dumps(result.data, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as e:
            # 序列化失败（如循环引用），同样回注错误而非误判 success
            logger.warning("工具 %s 结果序列化失败: %s", t.name, e)
            return f"错误: 结果序列化失败: {e}"

    return StructuredTool.from_function(
        coroutine=_run, name=t.name, description=t.description,
        args_schema=_schema_to_model(t.name, t.parameters))


def _role_to_message(role: str, content: str) -> BaseMessage:
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        return AIMessage(content=content)
    return SystemMessage(content=content)  # system / tool 历史无 tool_call_id，统一降级


class ReActExecutor:
    """自主循环执行器 — 模型自主决定工具调用时机，直到它认为任务完成"""

    def __init__(self, registry: ToolRegistry, memory: Memory, model=None,
                 session_id: str = "", persist_dir: str | None = None,
                 recursion_limit: int = 50):
        self._registry = registry
        self._memory = memory
        self._model = model
        self._session_id = session_id
        self._persist_dir = persist_dir
        self._recursion_limit = recursion_limit

    async def run(self, user_input: str,
                  on_event: EventCallback | None = None) -> AgentOutcome:
        if self._model is None:
            raise RuntimeError("LangChain 模型未注入，无法执行自主循环")

        from agent.graph import close_checkpointer, create_checkpointer

        history: list[BaseMessage] = [
            _role_to_message(m["role"], m["content"])
            for m in self._memory.get_context_window(n=_HISTORY_WINDOW)
        ]
        # app.py 入口已把用户消息写入 memory；重复注入会让模型上下文重复
        if not (history and isinstance(history[-1], HumanMessage)
                and history[-1].content == user_input):
            history.append(HumanMessage(content=user_input))

        lc_tools = [_as_lc_tool(t) for t in self._registry.list_all()]
        checkpointer = create_checkpointer(self._persist_dir)
        try:
            agent = create_react_agent(self._model, lc_tools,
                                       checkpointer=checkpointer)
        except Exception:  # 构造失败须关闭已建连接后重抛
            conn = getattr(checkpointer, "conn", None)
            if conn is not None:
                await conn.close()
            raise
        # 每次 run 均从头注入完整历史，checkpointer 仅作流式状态载体，无恢复用途；
        # thread_id 必须唯一，否则空 session 共享线程且消息跨轮累积
        config: RunnableConfig = {
            "configurable": {"thread_id": f"{self._session_id or 'react'}-{uuid.uuid4().hex[:8]}"},
            "recursion_limit": self._recursion_limit,
        }

        tool_calls: list[dict] = []
        # run_id → {tool, args}：单条消息多 tool_call 时 LangGraph 并行执行，
        # on_tool_start/on_tool_end 交错，必须按 run_id 配对防串名
        pending_tools: dict[str, dict] = {}
        final_reply = ""
        try:
            async for event in agent.astream_events(
                    {"messages": history}, config=config, version="v2"):
                etype = event.get("event")
                run_id = str(event.get("run_id", ""))
                if etype == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    content = getattr(chunk, "content", "") if chunk else ""
                    # 工具调用参数流不当作推理文本；空内容跳过（节流）
                    if content and not getattr(chunk, "tool_call_chunks", None) and on_event:
                        on_event({"type": "thinking", "content": content})
                elif etype == "on_tool_start":
                    data = event.get("data", {})
                    args = data.get("input", {})
                    # langgraph 1.x 事件中工具名在顶层 name 字段（data 仅含 input/output）
                    pending_tools[run_id] = {
                        "tool": event.get("name", "tool"), "args": args}
                    if on_event:
                        on_event({"type": "tool_call", "run_id": run_id,
                                  "tool": pending_tools[run_id]["tool"],
                                  "args": args})
                elif etype == "on_tool_end":
                    data = event.get("data", {})
                    # langgraph 1.x 的 output 为 ToolMessage，取其 content 再判状态
                    raw_output = data.get("output", "") or ""
                    output = str(getattr(raw_output, "content", raw_output))
                    entry = pending_tools.pop(run_id, None)
                    name = entry["tool"] if entry else event.get("name", "tool")
                    status = "error" if output.startswith("错误:") else "success"
                    summary = output[:_SUMMARY_LIMIT]
                    message_id = self._memory.add_message(
                        "tool", f"[{name}] {status}: {summary}")
                    tool_calls.append({"tool": name,
                                       "args": entry["args"] if entry else {},
                                       "status": status, "summary": summary,
                                       "raw_output": output,
                                       "message_id": message_id})
                    if on_event:
                        on_event({"type": "tool_result", "run_id": run_id,
                                  "tool": name, "content": summary,
                                  "message_id": message_id})
            # 必须在关闭 checkpointer 之前读取终态（SQLite 连接关闭后无法查询）
            state = await agent.aget_state(config)
            for msg in reversed(state.values.get("messages", [])):
                if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                    final_reply = str(msg.content)
                    break
        finally:
            await close_checkpointer(agent)
        if not final_reply:
            final_reply = "（模型未给出回答）"
        self._memory.add_message("assistant", final_reply)
        return AgentOutcome(final_reply=final_reply, tool_calls=tool_calls)

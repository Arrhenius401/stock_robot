"""ToolSelector — LLM 工具决策（原生 tool calling）+ 关键词降级"""
import logging
import re
from typing import Any

from agent.memory import TaskStep
from agent.tools import ToolRegistry

logger = logging.getLogger(__name__)

_SYMBOL_PATTERN = re.compile(r"\d{6}")

# 仅处理带 symbol 参数的确定性工具的降级参数提取
_SYMBOL_TOOLS = ("analyze_stock", "analyze_index", "get_snapshot")

_MAX_CANDIDATES = 4

TOOL_SELECTION_SYSTEM_PROMPT = """你是股票投研 Agent 的执行器。给定当前步骤、已完成步骤的结果和候选工具，选择最合适的工具并生成参数。

## 规则
1. 必须从候选工具中选择一个
2. 参数严格按工具 schema 生成，缺失的必填参数用合理默认值（如 top_k=5）
3. 只选择工具，不要执行，不要解释"""


def _extract_tool_args(tool_name: str, description: str) -> dict:
    """从步骤描述提取工具参数；仅处理带 symbol 参数的确定性工具"""
    if tool_name in _SYMBOL_TOOLS:
        m = _SYMBOL_PATTERN.search(description)
        if m:
            return {"symbol": m.group(0)}
    return {}


class ToolSelector:
    """步骤 → (tool_name, tool_args)；无法决策返回 (None, None)

    降级阶梯: LLM tool calling → 关键词匹配（ToolRegistry.match）→ None
    """

    def __init__(self, registry: ToolRegistry, model: Any = None):
        self._registry = registry
        self._model = model  # LangChain ChatModel（第三方对象，类型不定）

    async def select(self, step: TaskStep, decision_history: list[dict],
                     tool_results: list[dict]) -> tuple[str | None, dict | None]:
        """决策一步的工具与参数；返回 (None, None) 表示无法决策"""
        if self._model is None:
            return self._keyword_fallback(step)
        try:
            chosen = await self._llm_select(step, tool_results)
            if chosen is not None:
                return chosen
        except Exception as e:  # noqa: BLE001 — LLM 边界异常统一降级
            logger.warning("LLM 工具决策失败，降级关键词匹配: %s", e)
        return self._keyword_fallback(step)

    async def _llm_select(self, step: TaskStep,
                          tool_results: list[dict]) -> tuple[str, dict] | None:
        candidates = self._registry.match(step.description)[:_MAX_CANDIDATES]
        if not candidates:
            return None

        bound = self._model.bind_tools([
            {"name": t.name, "description": t.description,
             "parameters": t.parameters}
            for t in candidates
        ])
        messages = self._build_messages(step, tool_results)
        response = await bound.ainvoke(messages)

        valid_names = {t.name for t in candidates}
        for tc in response.tool_calls:
            if tc.get("name") in valid_names:
                return tc["name"], tc.get("args") or {}

        # 一次强制重试：明确候选范围
        messages = messages + [
            {"role": "assistant", "content": "（未选择合法工具）"},
            {"role": "user", "content": "只能从候选工具中选择一个: "
                                        + ", ".join(sorted(valid_names))},
        ]
        response = await bound.ainvoke(messages)
        for tc in response.tool_calls:
            if tc.get("name") in valid_names:
                return tc["name"], tc.get("args") or {}
        return None

    def _build_messages(self, step: TaskStep, tool_results: list[dict]) -> list[dict]:
        parts = [f"## 当前步骤\n{step.description}"]
        if tool_results:
            recent = tool_results[-3:]
            lines = []
            for r in recent:
                lines.append(f"  [{r.get('step_id', '')}] {r.get('status', '')}: "
                             f"{r.get('data') or r.get('error')}")
            parts.append("## 最近工具结果\n" + "\n".join(lines))
        return [
            {"role": "system", "content": TOOL_SELECTION_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(parts)},
        ]

    def _keyword_fallback(self, step: TaskStep) -> tuple[str | None, dict | None]:
        candidates = self._registry.match(step.description)
        if not candidates:
            return None, None
        tool = candidates[0]
        return tool.name, _extract_tool_args(tool.name, step.description)

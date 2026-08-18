"""Executor — 逐步执行引擎，负责工具匹配、执行编排、失败隔离"""
import logging
from collections.abc import Callable

from agent.memory import Memory, Plan, TaskStatus, TaskStep
from agent.tool_selector import _extract_tool_args
from agent.tools import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int, str], None] | None


class Executor:
    """逐步执行 Plan 中的 TaskStep

    负责:
    1. 拓扑排序执行（无依赖的步骤可并行）
    2. 工具匹配（通过 ToolRegistry）
    3. 失败隔离（单步失败 → 级联 skip 依赖步骤，其余继续）
    """

    def __init__(self, registry: ToolRegistry, memory: Memory, llm=None):
        self._registry = registry
        self._memory = memory
        self._llm = llm  # 预留：LLM 二次校验工具匹配

    async def execute(self, plan: Plan,
                      on_progress: ProgressCallback = None) -> Plan:
        """执行 Plan，返回更新后的 Plan（含步骤状态和结果）"""
        self._memory.add_plan(plan)
        self._memory.add_message("system", f"开始执行计划: {plan.goal}")

        while not plan.all_done():
            pending = plan.get_pending_steps()
            if not pending:
                break

            total = len(plan.steps)
            for step in pending:
                idx = plan.steps.index(step) + 1
                if on_progress:
                    on_progress("execute", idx, total, step.description)
                await self._execute_step(step)
                if step.status == TaskStatus.FAILED:
                    plan.mark_dependents_skipped(step.id)

        self._memory.add_message("system", f"计划执行完成: {plan.goal}")
        if on_progress:
            on_progress("complete", len(plan.steps), len(plan.steps), "计划执行完成")

        return plan

    async def _execute_step(self, step: TaskStep) -> None:
        step.status = TaskStatus.RUNNING
        self._memory.add_message("system", f"执行: {step.description}")

        if step.tool_name:
            tool = self._registry.get(step.tool_name)
        else:
            candidates = self._registry.match(step.description)
            tool = candidates[0] if candidates else None

        if tool is None:
            step.status = TaskStatus.FAILED
            self._memory.add_message(
                "system",
                f"步骤 {step.id} 失败: 找不到匹配的工具 [{step.description}]",
            )
            return

        step.tool_name = tool.name
        if not step.tool_args:
            step.tool_args = _extract_tool_args(tool.name, step.description)
        result = await self._safe_execute(tool, step.tool_args or {})

        if result.status == "error":
            step.status = TaskStatus.FAILED
            self._memory.add_message("system", f"步骤 {step.id} 失败: {result.error}")
        else:
            step.status = TaskStatus.DONE
            self._memory.add_message(
                "tool",
                f"[{tool.name}] {result.status}: {result.data}",
            )

    async def _safe_execute(self, tool, kwargs: dict) -> ToolResult:
        try:
            return await tool.execute(**kwargs)
        except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
            logger.error(f"工具 {tool.name} 执行异常: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": getattr(tool, "source", "unknown")})

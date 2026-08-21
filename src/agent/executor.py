"""Executor — LangGraph 执行图包装，保留逐步执行、失败隔离语义"""
import logging
from collections.abc import Callable

from agent.graph import (
    build_execution_graph,
    close_checkpointer,
    plan_to_state,
    state_to_plan,
)
from agent.memory import Memory, Plan
from agent.tools import ToolRegistry

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int, str], None] | None


class Executor:
    """逐步执行 Plan 中的 TaskStep（由 LangGraph 状态图驱动）

    工具决策：LLM tool calling（model 注入时）→ ToolRegistry 关键词匹配降级。
    失败隔离：单步失败 → 级联 skip 依赖步骤，其余继续。
    连接生命周期：每次 execute 构建图，finally 中显式关闭 checkpointer 连接
    （aiosqlite 连接不会被 GC 回收）。
    """

    def __init__(self, registry: ToolRegistry, memory: Memory, model=None,
                 session_id: str = "", persist_dir: str | None = None):
        self._registry = registry
        self._memory = memory
        self._model = model
        self._session_id = session_id
        # persist_dir=None 时用内存 checkpointer（测试/无会话场景）
        self._persist_dir = persist_dir

    async def execute(self, plan: Plan,
                      on_progress: ProgressCallback = None) -> Plan:
        """执行 Plan，返回更新后的 Plan（含步骤状态和结果）"""
        self._memory.add_plan(plan)
        self._memory.add_message("system", f"开始执行计划: {plan.goal}")

        graph = build_execution_graph(
            self._registry, self._memory, self._model, self._persist_dir,
            on_progress=on_progress)
        state = plan_to_state(plan, self._session_id)
        from langchain_core.runnables import RunnableConfig
        config: RunnableConfig = {
            "configurable": {"thread_id": self._session_id or "cli"}}
        try:
            await graph.ainvoke(state, config)  # 异步节点须用 ainvoke
        finally:
            await close_checkpointer(graph)

        state_to_plan(state, plan)
        self._memory.add_message("system", f"计划执行完成: {plan.goal}")
        return plan

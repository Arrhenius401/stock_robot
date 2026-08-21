"""LangGraph 执行图 — 决策→执行→反馈条件循环，带 checkpointer 持久化"""
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END

from agent.memory import Memory, Plan, TaskStatus, TaskStep
from agent.tool_selector import ToolSelector
from agent.tools import ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_CHECKPOINT_DIR = Path.home() / ".stock_robot" / "langgraph_checkpoints.sqlite"


class GraphState(TypedDict):
    goal: str
    steps: list[dict]            # [{id, description, tool_name, tool_args, status, depends_on}]
    pending_ids: list[str]
    done_ids: list[str]
    failed_ids: list[str]
    skipped_ids: list[str]
    decision_history: list[dict]
    tool_results: list[dict]


def plan_to_state(plan: Plan, session_id: str = "") -> GraphState:
    # session_id 预留：会话标识（Task 7 Executor 传入）
    return {
        "goal": plan.goal,
        "steps": [{
            "id": s.id, "description": s.description,
            "tool_name": s.tool_name, "tool_args": s.tool_args,
            "status": s.status.value, "depends_on": list(s.depends_on),
        } for s in plan.steps],
        "pending_ids": [s.id for s in plan.get_pending_steps()],
        "done_ids": [], "failed_ids": [], "skipped_ids": [],
        "decision_history": [], "tool_results": [],
    }


def state_to_plan(state: GraphState, plan: Plan) -> Plan:
    """把图状态写回 Plan（步骤状态/工具选择），保持原步骤对象顺序"""
    by_id = {s["id"]: s for s in state["steps"]}
    for step in plan.steps:
        snap = by_id.get(step.id)
        if snap is None:
            continue
        step.tool_name = snap.get("tool_name")
        step.tool_args = snap.get("tool_args")
        step.status = TaskStatus(snap.get("status", "pending"))
    return plan


def create_checkpointer(persist_dir: str | None) -> Any:
    """AsyncSqliteSaver 持久化；初始化失败降级 MemorySaver

    langgraph-checkpoint-sqlite 3.x 中同步 SqliteSaver 不支持异步方法
    （ainvoke 抛 NotImplementedError），必须用 AsyncSqliteSaver；
    from_conn_string 是 async 上下文管理器不能直接作为 saver 返回，改为
    自建 aiosqlite 连接传入 AsyncSqliteSaver(conn)。aiosqlite.connect
    同步返回惰性启动的连接代理（setup() 时 await 启动），但构造函数需要
    运行中的事件循环（asyncio.get_running_loop）——build_execution_graph
    在 async 执行路径（Executor.execute / 测试）中调用，前提满足；无事件
    循环时降级 MemorySaver。连接由调用方负责显式关闭（close_checkpointer）：
    aiosqlite 连接不会被 GC 回收，事件循环关闭时其工作线程会报错退出。
    """
    if persist_dir is None:
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
    try:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        conn = aiosqlite.connect(str(persist_dir))
        return AsyncSqliteSaver(conn)
    except Exception as e:  # noqa: BLE001 — checkpointer 失败降级内存
        logger.warning("AsyncSqliteSaver 初始化失败 (%s)，降级 MemorySaver", e)
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()


async def close_checkpointer(graph) -> None:
    """显式关闭 checkpointer 连接（aiosqlite 连接不会被 GC 关闭）

    在 Executor 执行结束后调用；MemorySaver 无 conn 属性，安全跳过。
    """
    saver = getattr(graph, "checkpointer", None)
    conn = getattr(saver, "conn", None)
    if conn is None:
        return
    try:
        await conn.close()
    except Exception:  # noqa: BLE001 — 关闭失败不影响执行结果
        logger.debug("关闭 checkpointer 连接失败")


def _step_by_id(state: GraphState, step_id: str) -> dict | None:
    for s in state["steps"]:
        if s["id"] == step_id:
            return s
    return None


def _recompute_pending(state: GraphState) -> list[str]:
    """重算可执行步骤：pending 且依赖全部终态（done/skipped）

    输入 state 需保证级联一致：failed 依赖的级联 skip 由 _cascade_skip 维护，
    手工构造含 failed 依赖的 state 时不做防御性 skip。
    """
    terminal = set(state["done_ids"]) | set(state["skipped_ids"])
    ready = []
    for s in state["steps"]:
        if s["status"] != "pending":
            continue
        if all(dep in terminal for dep in s.get("depends_on", [])):
            ready.append(s["id"])
    return ready


def _cascade_skip(state: GraphState, failed_id: str) -> None:
    """失败步骤的依赖者级联标记 skipped（递归）"""
    for s in state["steps"]:
        if s["status"] != "pending":
            continue
        if failed_id in s.get("depends_on", []):
            s["status"] = "skipped"
            state["skipped_ids"].append(s["id"])
            _cascade_skip(state, s["id"])


def route_from_decide(state: GraphState) -> str:
    """decide 后路由：无 tool_name（决策失败）直接进 feedback 处理失败逻辑"""
    if not state["pending_ids"]:
        return END
    step = _step_by_id(state, state["pending_ids"][0])
    return "execute" if step is not None and step.get("tool_name") else "feedback"


def build_execution_graph(registry: ToolRegistry, memory: Memory, model=None,
                          persist_dir: str | None = None,
                          on_progress: Callable[[str, int, int, str], None] | None = None) -> Any:
    """构建 LangGraph 执行图（决策→执行→反馈循环）

    persist_dir=None 用内存 checkpointer（测试/无会话场景）。
    """
    from langgraph.graph import END, START, StateGraph

    selector = ToolSelector(registry, model)

    async def decide_node(state: GraphState) -> dict:
        if not state["pending_ids"]:
            return {}
        step = _step_by_id(state, state["pending_ids"][0])
        assert step is not None

        chosen_tool = step.get("tool_name")
        chosen_args = step.get("tool_args")
        reason = "plan"
        if chosen_tool is None:
            chosen_tool, chosen_args, source = await selector.select(
                TaskStep(id=step["id"], description=step["description"]),
                state["decision_history"], state["tool_results"])
            if chosen_tool is None:
                return {"steps": state["steps"]}
            reason = source
        step["tool_name"] = chosen_tool
        step["tool_args"] = chosen_args or {}
        state["decision_history"].append({
            "step_id": step["id"], "chosen_tool": chosen_tool, "reason": reason})
        return {"steps": state["steps"],
                "decision_history": state["decision_history"]}

    async def execute_node(state: GraphState) -> dict:
        if not state["pending_ids"]:
            return {}
        step = _step_by_id(state, state["pending_ids"][0])
        assert step is not None and step.get("tool_name")
        tool = registry.get(step["tool_name"])
        result = await _safe_execute(tool, step.get("tool_args") or {})
        state["tool_results"].append({
            "step_id": step["id"], "status": result.status,
            "data": result.data, "error": result.error,
        })
        return {"tool_results": state["tool_results"]}

    def feedback_node(state: GraphState) -> dict:
        if not state["pending_ids"]:
            return {}
        step_id = state["pending_ids"][0]
        step = _step_by_id(state, step_id)
        assert step is not None
        result = next((r for r in state["tool_results"]
                       if r["step_id"] == step_id), None)
        memory.add_message("system", f"执行: {step['description']}")
        if on_progress:
            idx = next((i for i, s in enumerate(state["steps"])
                        if s["id"] == step_id), 0) + 1
            on_progress("execute", idx, len(state["steps"]), step["description"])
        if result is None or result["status"] == "error":
            if step["status"] != "failed":
                step["status"] = "failed"
            if step_id not in state["failed_ids"]:
                state["failed_ids"].append(step_id)
            memory.add_message(
                "system", f"步骤 {step_id} 失败: "
                          f"{result and result['error'] or '决策失败（无候选工具）'}")
            _cascade_skip(state, step_id)
        else:
            if step["status"] != "done":
                step["status"] = "done"
            if step_id not in state["done_ids"]:
                state["done_ids"].append(step_id)
            memory.add_message(
                "tool", f"[{step.get('tool_name')}] {result['status']}: "
                        f"{result['data']}")
        state["pending_ids"] = _recompute_pending(state)
        return {"steps": state["steps"], "pending_ids": state["pending_ids"],
                "done_ids": state["done_ids"], "failed_ids": state["failed_ids"],
                "skipped_ids": state["skipped_ids"]}

    def route(state: GraphState) -> str:
        return "decide" if state["pending_ids"] else END

    builder = StateGraph(GraphState)
    builder.add_node("decide", decide_node)
    builder.add_node("execute", execute_node)
    builder.add_node("feedback", feedback_node)
    builder.add_edge(START, "decide")
    builder.add_conditional_edges("decide", route_from_decide,
                                  {"execute": "execute", "feedback": "feedback",
                                   END: END})
    builder.add_edge("execute", "feedback")
    builder.add_conditional_edges("feedback", route,
                                  {"decide": "decide", END: END})
    return builder.compile(checkpointer=create_checkpointer(persist_dir))


async def _safe_execute(tool, kwargs: dict) -> Any:
    """工具执行隔离：异常转为 error 结果"""
    if tool is None:
        from agent.tools import ToolResult
        return ToolResult(status="error", error="工具不存在")
    try:
        return await tool.execute(**kwargs)
    except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
        from agent.tools import ToolResult
        logger.error("工具 %s 执行异常: %s", tool.name, e)
        return ToolResult(status="error", error=str(e),
                          metadata={"source": getattr(tool, "source", "unknown")})

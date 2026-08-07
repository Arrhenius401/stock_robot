"""输出渲染 — 协议定义 + Rich/JSON 实现"""
import json
from typing import Any, Protocol
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from agent.memory import Plan, TaskStep, TaskStatus
from agent.tools import ToolResult


class OutputRenderer(Protocol):
    """多端渲染协议 — CLI(Rich) / API(JSON) / Web(HTML)"""

    def render_plan(self, plan: Plan) -> str: ...
    def render_progress(self, step: TaskStep, step_index: int, total: int) -> str: ...
    def render_tool_result(self, tool_name: str, result: ToolResult) -> str: ...
    def render_error(self, error: str) -> str: ...
    def render_summary(self, plan: Plan) -> str: ...


class RichRenderer:
    def __init__(self, console: Console | None = None):
        self._console = console or Console()

    def render_plan(self, plan: Plan) -> str:
        table = Table(title=f"执行计划: {plan.goal}")
        table.add_column("步骤", style="cyan", width=6)
        table.add_column("描述", style="white")
        table.add_column("依赖", style="dim", width=20)
        table.add_column("状态", style="yellow", width=10)

        for s in plan.steps:
            deps = ", ".join(s.depends_on) if s.depends_on else "—"
            status_icon = _status_icon(s.status)
            table.add_row(s.id, s.description, deps, f"{status_icon} {s.status}")

        console = Console()
        with console.capture() as capture:
            console.print(table)
        return capture.get().strip()

    def render_progress(self, step: TaskStep, step_index: int, total: int) -> str:
        icon = _status_icon(step.status)
        return f"{icon} 步骤 {step_index}/{total}: {step.description}"

    def render_tool_result(self, tool_name: str, result: ToolResult) -> str:
        if result.status == "success":
            label = "成功"
        elif result.status == "error":
            label = f"失败 — {result.error}"
        else:
            label = "部分成功"
        return f"  工具 {tool_name}: {label}"

    def render_error(self, error: str) -> str:
        console = Console()
        with console.capture() as capture:
            console.print(Panel(error, title="错误", border_style="red"))
        return capture.get().strip()

    def render_summary(self, plan: Plan) -> str:
        done = sum(1 for s in plan.steps if s.status == TaskStatus.DONE)
        failed = sum(1 for s in plan.steps if s.status == TaskStatus.FAILED)
        total = len(plan.steps)
        msg = f"目标: {plan.goal}\n完成: {done}/{total} 步骤"
        if failed > 0:
            msg += f"（{failed} 失败）"
        console = Console()
        with console.capture() as capture:
            console.print(Panel(
                msg,
                title="执行完成",
                border_style="green" if failed == 0 else "yellow",
            ))
        return capture.get().strip()


class JsonRenderer:
    def render_plan(self, plan: Plan) -> str:
        return json.dumps({
            "goal": plan.goal,
            "steps": [
                {
                    "id": s.id, "description": s.description,
                    "depends_on": s.depends_on, "status": s.status,
                }
                for s in plan.steps
            ],
        }, ensure_ascii=False)

    def render_progress(self, step: TaskStep, step_index: int, total: int) -> str:
        return json.dumps({
            "step_id": step.id,
            "description": step.description,
            "index": step_index,
            "total": total,
            "status": step.status,
        }, ensure_ascii=False)

    def render_tool_result(self, tool_name: str, result: ToolResult) -> str:
        return json.dumps({
            "tool": tool_name,
            "status": result.status,
            "data": result.data,
            "error": result.error,
        }, ensure_ascii=False, default=str)

    def render_error(self, error: str) -> str:
        return json.dumps({"error": error}, ensure_ascii=False)

    def render_summary(self, plan: Plan) -> str:
        done = sum(1 for s in plan.steps if s.status == TaskStatus.DONE)
        failed = sum(1 for s in plan.steps if s.status == TaskStatus.FAILED)
        return json.dumps({
            "goal": plan.goal,
            "total_steps": len(plan.steps),
            "done": done,
            "failed": failed,
        }, ensure_ascii=False)


def _status_icon(status: str) -> str:
    icons = {
        TaskStatus.PENDING: "○",
        TaskStatus.RUNNING: "◉",
        TaskStatus.DONE: "✓",
        TaskStatus.FAILED: "✗",
        TaskStatus.SKIPPED: "—",
    }
    return icons.get(status, "?")

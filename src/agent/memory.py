"""Agent Memory — 对话记忆、计划历史、事实持久化"""
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskStep:
    id: str                           # 如 "step-1"
    description: str                  # 如 "筛选新能源龙头股"
    tool_name: str | None = None      # 计划阶段可为空，执行时由 Executor 匹配填充
    tool_args: dict | None = None
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING


@dataclass
class Plan:
    goal: str                          # 原始用户意图
    steps: list[TaskStep]
    context_summary: str = ""          # 从 Memory 提取的相关历史摘要
    mode: Literal["task", "chat"] = "task"   # task=拆计划执行；chat=普通会话

    def get_pending_steps(self) -> list[TaskStep]:
        """返回当前可执行的步骤（依赖已满足且状态为 pending）"""
        done_or_skipped = {
            s.id for s in self.steps
            if s.status in (TaskStatus.DONE, TaskStatus.SKIPPED)
        }
        ready = []
        for s in self.steps:
            if s.status != TaskStatus.PENDING:
                continue
            if all(dep in done_or_skipped for dep in s.depends_on):
                ready.append(s)
        return ready

    def mark_dependents_skipped(self, failed_step_id: str) -> None:
        """将依赖失败步骤的所有步骤标记为 skipped"""
        for s in self.steps:
            if s.status != TaskStatus.PENDING:
                continue
            if failed_step_id in s.depends_on:
                s.status = TaskStatus.SKIPPED
                # 递归标记间接依赖
                self.mark_dependents_skipped(s.id)

    def all_done(self) -> bool:
        """所有步骤是否已终态（done/skipped/failed）"""
        return all(
            s.status in (TaskStatus.DONE, TaskStatus.SKIPPED, TaskStatus.FAILED)
            for s in self.steps
        )


class Memory:
    """Agent 记忆 —— 三层存储：会话消息 / 计划历史 / 持久化 facts"""

    def __init__(self, max_messages: int = 30, facts_path: Path | None = None,
                 session_id: str | None = None, message_store=None):
        self._max_messages = max_messages
        self.messages: list[dict[str, str]] = []
        self.plan_history: list[Plan] = []
        self.facts: dict[str, Any] = {}
        self.session_id = session_id
        self._message_store = message_store
        if facts_path is None:
            facts_path = Path.home() / ".stock_robot" / "agent_facts.json"
        self._facts_path = Path(facts_path)
        self._load_facts()

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        if len(self.messages) > self._max_messages:
            self.messages = self.messages[-self._max_messages:]
        if self._message_store is not None and self.session_id:
            self._message_store.append_message(self.session_id, role, content)

    def add_plan(self, plan: Plan) -> None:
        self.plan_history.append(plan)

    def get_last_plan(self) -> Plan | None:
        return self.plan_history[-1] if self.plan_history else None

    def get_context_window(self, n: int = 20) -> list[dict]:
        """返回最近 N 轮对话，供 Planner 使用"""
        return self.messages[-n:] if len(self.messages) > n else list(self.messages)

    def set_fact(self, key: str, value: Any) -> None:
        self.facts[key] = value
        self._persist_facts()

    def get_fact(self, key: str) -> Any | None:
        return self.facts.get(key)

    def clear_session(self) -> None:
        """清空会话上下文，保留 facts"""
        self.messages = []
        self.plan_history = []

    def _load_facts(self) -> None:
        if self._facts_path.exists():
            try:
                self.facts = json.loads(self._facts_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.facts = {}

    def _persist_facts(self) -> None:
        self._facts_path.parent.mkdir(parents=True, exist_ok=True)
        self._facts_path.write_text(
            json.dumps(self.facts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

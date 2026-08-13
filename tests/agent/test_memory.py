"""Memory 数据结构与持久化测试"""
import json
from pathlib import Path

from agent.memory import Memory, Plan, TaskStatus, TaskStep


class TestTaskStatus:
    def test_known_statuses(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.DONE == "done"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.SKIPPED == "skipped"


class TestTaskStep:
    def test_create_minimal_step(self):
        step = TaskStep(id="step-1", description="分析茅台财务")
        assert step.id == "step-1"
        assert step.description == "分析茅台财务"
        assert step.tool_name is None
        assert step.tool_args is None
        assert step.depends_on == []
        assert step.status == TaskStatus.PENDING

    def test_create_step_with_dependencies(self):
        step = TaskStep(
            id="step-2",
            description="估值对比",
            depends_on=["step-1"],
            tool_name="analyze_stock",
            tool_args={"symbol": "600519"},
        )
        assert step.depends_on == ["step-1"]
        assert step.tool_name == "analyze_stock"
        assert step.tool_args == {"symbol": "600519"}

    def test_step_status_transitions(self):
        step = TaskStep(id="s1", description="采集数据")
        step.status = TaskStatus.RUNNING
        assert step.status == "running"
        step.status = TaskStatus.DONE
        assert step.status == "done"

    def test_step_default_status_is_pending(self):
        step = TaskStep(id="s1", description="test")
        assert step.status == "pending"


class TestPlan:
    def test_create_plan_with_steps(self):
        step1 = TaskStep(id="step-1", description="筛选标的")
        step2 = TaskStep(id="step-2", description="采集数据", depends_on=["step-1"])
        plan = Plan(
            goal="找3只被低估的新能源龙头",
            steps=[step1, step2],
            context_summary="用户偏好科技股，风险偏好中等",
        )
        assert plan.goal == "找3只被低估的新能源龙头"
        assert len(plan.steps) == 2
        assert plan.steps[1].depends_on == ["step-1"]
        assert plan.context_summary == "用户偏好科技股，风险偏好中等"

    def test_empty_plan(self):
        plan = Plan(goal="测试", steps=[], context_summary="")
        assert len(plan.steps) == 0

    def test_get_pending_steps(self):
        s1 = TaskStep(id="s1", description="a", status=TaskStatus.DONE)
        s2 = TaskStep(id="s2", description="b", status=TaskStatus.PENDING)
        s3 = TaskStep(id="s3", description="c", status=TaskStatus.PENDING, depends_on=["s2"])
        plan = Plan(goal="test", steps=[s1, s2, s3], context_summary="")

        pending = plan.get_pending_steps()
        # s2 先于 s3（无依赖的优先）
        assert [s.id for s in pending] == ["s2"]

    def test_get_pending_steps_returns_independent_first(self):
        s1 = TaskStep(id="s1", description="a")
        s2 = TaskStep(id="s2", description="b", depends_on=["s1"])
        s3 = TaskStep(id="s3", description="c")
        plan = Plan(goal="test", steps=[s1, s2, s3], context_summary="")

        pending = plan.get_pending_steps()
        ids = [s.id for s in pending]
        # 无依赖的 s1, s3 优先
        assert ids[0] in ("s1", "s3")
        assert ids[1] in ("s1", "s3")

    def test_mark_failed_cascades_to_dependents(self):
        s1 = TaskStep(id="s1", description="a", status=TaskStatus.RUNNING)
        s2 = TaskStep(id="s2", description="b", depends_on=["s1"])
        s3 = TaskStep(id="s3", description="c")
        plan = Plan(goal="test", steps=[s1, s2, s3], context_summary="")

        s1.status = TaskStatus.FAILED
        plan.mark_dependents_skipped("s1")

        assert s2.status == TaskStatus.SKIPPED
        assert s3.status == TaskStatus.PENDING  # 独立步骤不受影响

    def test_all_done_returns_true_when_all_steps_completed(self):
        s1 = TaskStep(id="s1", description="a", status=TaskStatus.DONE)
        s2 = TaskStep(id="s2", description="b", status=TaskStatus.SKIPPED)
        plan = Plan(goal="test", steps=[s1, s2], context_summary="")
        assert plan.all_done()


class TestMemory:
    def test_create_empty_memory(self, tmp_path):
        facts_file = tmp_path / "facts.json"
        m = Memory(facts_path=facts_file)
        assert m.messages == []
        assert m.plan_history == []
        assert m.facts == {}

    def test_add_message_trims_window(self):
        m = Memory(max_messages=3)
        for i in range(5):
            m.add_message("user", f"msg {i}")

        assert len(m.messages) == 3
        assert m.messages[0]["content"] == "msg 2"
        assert m.messages[-1]["content"] == "msg 4"

    def test_add_message_stores_role_and_content(self):
        m = Memory()
        m.add_message("user", "测试问题")
        m.add_message("assistant", "测试回答")

        assert m.messages[0] == {"role": "user", "content": "测试问题"}
        assert m.messages[1] == {"role": "assistant", "content": "测试回答"}

    def test_add_plan_appends_to_history(self):
        m = Memory()
        plan = Plan(goal="test", steps=[], context_summary="")
        m.add_plan(plan)
        assert len(m.plan_history) == 1

    def test_get_last_plan_returns_most_recent(self):
        m = Memory()
        plan1 = Plan(goal="first", steps=[], context_summary="")
        plan2 = Plan(goal="second", steps=[], context_summary="")
        m.add_plan(plan1)
        m.add_plan(plan2)
        last = m.get_last_plan()
        assert last is not None
        assert last.goal == "second"

    def test_get_last_plan_returns_none_when_empty(self):
        m = Memory()
        assert m.get_last_plan() is None

    def test_get_context_window_returns_recent_messages(self):
        m = Memory(max_messages=10)
        m.add_message("user", "问题1")
        m.add_message("assistant", "回答1")
        m.add_message("user", "问题2")

        ctx = m.get_context_window(n=2)
        assert len(ctx) == 2
        assert ctx[0]["content"] == "回答1"
        assert ctx[1]["content"] == "问题2"

    def test_set_fact_stores_and_persists(self, tmp_path):
        facts_file = tmp_path / "facts.json"
        m = Memory(facts_path=facts_file)
        m.set_fact("preferred_style", "growth")
        assert m.facts["preferred_style"] == "growth"
        assert facts_file.exists()

        loaded = json.loads(facts_file.read_text(encoding="utf-8"))
        assert loaded["preferred_style"] == "growth"

    def test_load_facts_from_existing_file(self, tmp_path):
        facts_file = tmp_path / "facts.json"
        facts_file.write_text(
            json.dumps({"preferred_style": "value", "favorite_stocks": ["600519"]}),
            encoding="utf-8",
        )
        m = Memory(facts_path=facts_file)
        assert m.facts["preferred_style"] == "value"
        assert m.facts["favorite_stocks"] == ["600519"]

    def test_get_fact_returns_none_for_missing_key(self, tmp_path):
        facts_file = tmp_path / "facts.json"
        m = Memory(facts_path=facts_file)
        assert m.get_fact("nonexistent") is None

    def test_get_fact_returns_stored_value(self, tmp_path):
        facts_file = tmp_path / "facts.json"
        m = Memory(facts_path=facts_file)
        m.set_fact("risk_tolerance", "high")
        assert m.get_fact("risk_tolerance") == "high"

    def test_clear_session_resets_messages_and_plans_only(self, tmp_path):
        facts_file = tmp_path / "facts.json"
        m = Memory(facts_path=facts_file)
        m.add_message("user", "test")
        plan = Plan(goal="test", steps=[], context_summary="")
        m.add_plan(plan)
        m.set_fact("key", "value")

        m.clear_session()
        assert m.messages == []
        assert m.plan_history == []
        assert m.facts == {"key": "value"}  # facts 保留

    def test_default_facts_path_is_in_config_dir(self, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: Path("/tmp"))
        m = Memory()
        assert str(m._facts_path).startswith(str(Path("/tmp") / ".stock_robot"))

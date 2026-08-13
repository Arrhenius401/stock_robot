"""OutputRenderer 协议与实现测试"""
import pytest

from agent.memory import Plan, TaskStatus, TaskStep
from agent.tools import ToolResult
from output.renderer import JsonRenderer, RichRenderer


class TestRichRenderer:
    @pytest.fixture
    def renderer(self):
        return RichRenderer()

    def test_render_plan_produces_table(self, renderer):
        plan = Plan(
            goal="找低估值股票",
            steps=[
                TaskStep(id="s1", description="筛选标的", status=TaskStatus.DONE),
                TaskStep(id="s2", description="采集数据", status=TaskStatus.RUNNING, depends_on=["s1"]),
                TaskStep(id="s3", description="估值对比", status=TaskStatus.PENDING, depends_on=["s2"]),
            ],
        )
        output = renderer.render_plan(plan)
        assert "找低估值股票" in output
        assert "筛选标的" in output
        assert "done" in output.lower()

    def test_render_progress_shows_step_info(self, renderer):
        step = TaskStep(id="s2", description="采集财务数据", status=TaskStatus.RUNNING)
        output = renderer.render_progress(step, step_index=1, total=3)
        assert "采集财务数据" in output
        assert "1/3" in output

    def test_render_error_includes_details(self, renderer):
        output = renderer.render_error("工具执行失败: 连接超时")
        assert "连接超时" in output

    def test_render_success_result_with_data(self, renderer):
        result = ToolResult(status="success", data={"pe": 15.5, "pb": 2.1})
        output = renderer.render_tool_result("analyze_stock", result)
        assert "analyze_stock" in output
        assert "成功" in output

    def test_render_error_result_with_error_message(self, renderer):
        result = ToolResult(status="error", error="AkShare API 调用失败")
        output = renderer.render_tool_result("fetch_data", result)
        assert "fetch_data" in output.lower()
        assert "失败" in output

    def test_render_final_summary_includes_goal_and_status(self, renderer):
        plan = Plan(
            goal="找3只被低估的新能源龙头",
            steps=[
                TaskStep(id="s1", description="筛选标的", status=TaskStatus.DONE),
                TaskStep(id="s2", description="分析", status=TaskStatus.DONE),
            ],
        )
        output = renderer.render_summary(plan)
        assert "找3只被低估的新能源龙头" in output
        assert "完成" in output


class TestJsonRenderer:
    @pytest.fixture
    def renderer(self):
        return JsonRenderer()

    def test_render_plan_returns_valid_json(self, renderer):
        import json
        plan = Plan(
            goal="测试",
            steps=[TaskStep(id="s1", description="步骤1")],
        )
        output = renderer.render_plan(plan)
        data = json.loads(output)
        assert data["goal"] == "测试"
        assert len(data["steps"]) == 1

    def test_render_tool_result_returns_valid_json(self, renderer):
        import json
        result = ToolResult(status="success", data={"price": 100})
        output = renderer.render_tool_result("test_tool", result)
        data = json.loads(output)
        assert data["tool"] == "test_tool"
        assert data["status"] == "success"

"""Planner 单元测试"""
import json

import pytest

from agent.memory import Memory
from agent.planner import CHAT_PHRASES, SIMPLE_QUERY_PREFIXES, Planner
from agent.tools import ToolRegistry


class FakeLLM:
    """返回预设 JSON 计划的 mock LLM"""
    def __init__(self, fixed_response=None):
        self._response = fixed_response
        self.calls = []

    @property
    def model_name(self):
        return "test-model"

    def generate(self, prompt, system=None, **kwargs):
        self.calls.append({"system": system, "prompt": prompt})
        return self._response or ""


def make_multi_step_response():
    return json.dumps({
        "goal": "找3只低估值新能源龙头股",
        "complexity": "complex",
        "steps": [
            {"id": "step-1", "description": "筛选新能源板块的龙头股候选"},
            {"id": "step-2", "description": "对每只候选股进行全维度分析", "depends_on": ["step-1"]},
            {"id": "step-3", "description": "综合对比评分排名", "depends_on": ["step-2"]},
        ],
    }, ensure_ascii=False)


def make_single_step_response():
    return json.dumps({
        "goal": "查询茅台最新股价",
        "complexity": "simple",
        "steps": [
            {"id": "step-1", "description": "查询贵州茅台最新行情"},
        ],
    }, ensure_ascii=False)


def make_chat_response():
    return json.dumps({
        "goal": "闲聊",
        "complexity": "simple",
        "mode": "chat",
        "steps": [],
    }, ensure_ascii=False)


def make_agent_response():
    return json.dumps({
        "goal": "对比分析",
        "complexity": "complex",
        "mode": "agent",
        "steps": [],
    }, ensure_ascii=False)


class TestPlanner:
    @pytest.fixture
    def registry(self):
        return ToolRegistry()

    @pytest.fixture
    def memory(self):
        return Memory()

    def test_simple_query_prefixes_list(self):
        assert isinstance(SIMPLE_QUERY_PREFIXES, list)
        assert len(SIMPLE_QUERY_PREFIXES) > 0

    def test_is_simple_query_matches_prefix(self):
        planner = Planner(llm=None, registry=None)
        assert planner._is_simple_query("什么是市盈率") is True
        assert planner._is_simple_query("最近茅台涨了多少") is True

    def test_is_simple_query_returns_false_for_complex(self):
        planner = Planner(llm=None, registry=None)
        assert planner._is_simple_query("帮我找3只被低估的新能源龙头") is False
        assert planner._is_simple_query("大盘现在适合入场吗") is False

    def test_plan_simple_query_returns_agent(self, registry, memory):
        """简单查询免 LLM 规划，直接 agent 自主执行以保证产出正文"""
        planner = Planner(llm=FakeLLM(), registry=registry, memory=memory)
        plan = planner.plan("什么是PE")

        assert plan.mode == "agent"
        assert plan.steps == []

    def test_plan_complex_query_calls_llm(self, registry, memory):
        llm = FakeLLM(fixed_response=make_multi_step_response())
        planner = Planner(llm=llm, registry=registry, memory=memory)

        plan = planner.plan("帮我分析新能源板块")

        assert len(llm.calls) > 0
        assert plan.goal == "找3只低估值新能源龙头股"
        assert len(plan.steps) == 3
        assert plan.steps[1].depends_on == ["step-1"]
        assert plan.mode == "plan"

    def test_plan_complex_query_injects_tool_list_in_system_prompt(self, registry, memory):
        class FakeAnalyzeTool:
            name = "analyze_stock"
            description = "分析股票"
            parameters = {"type": "object", "properties": {}}
            tags = ["pipeline"]
            source = "pipeline"
            async def execute(self, **kwargs): pass

        registry.register(FakeAnalyzeTool())

        llm = FakeLLM(fixed_response=make_multi_step_response())
        planner = Planner(llm=llm, registry=registry, memory=memory)
        planner.plan("帮我分析新能源板块")

        system_prompt = llm.calls[0]["system"]
        assert "analyze_stock" in system_prompt

    def test_plan_includes_facts_context(self, registry, memory):
        memory.set_fact("preferred_style", "growth")
        memory.set_fact("favorite_stocks", ["600519"])

        llm = FakeLLM(fixed_response=make_single_step_response())
        planner = Planner(llm=llm, registry=registry, memory=memory)
        plan = planner.plan("推荐股票")

        assert plan.context_summary != ""

    def test_plan_handles_llm_failure_gracefully(self, registry, memory):
        class FailingLLM:
            @property
            def model_name(self):
                return "failing-model"
            def generate(self, prompt, system=None, **kwargs):
                raise RuntimeError("API 不可用")

        planner = Planner(llm=FailingLLM(), registry=registry, memory=memory)
        plan = planner.plan("复杂分析任务")

        assert plan.mode == "agent"  # 降级走自主循环，保证模型产出正文
        assert plan.steps == []

    def test_plan_handles_malformed_json_response(self, registry, memory):
        llm = FakeLLM(fixed_response="这不是有效的 JSON 格式")
        planner = Planner(llm=llm, registry=registry, memory=memory)

        plan = planner.plan("分析市场")

        assert plan.mode == "agent"  # 降级走自主循环，保证模型产出正文
        assert plan.steps == []

    def test_plan_llm_empty_response_falls_back_to_agent(self, registry, memory):
        """LLM 无有效规划响应时降级 agent 自主循环，保证产出正文"""
        planner = Planner(llm=FakeLLM(), registry=registry, memory=memory)
        plan = planner.plan("茅台")

        assert plan.mode == "agent"
        assert plan.steps == []


class TestChatDetection:
    @pytest.fixture
    def registry(self):
        return ToolRegistry()

    @pytest.fixture
    def memory(self):
        return Memory()

    def test_chat_phrases_defined(self):
        assert isinstance(CHAT_PHRASES, set)
        assert len(CHAT_PHRASES) > 0

    def test_pure_politeness_is_chat(self):
        planner = Planner(llm=None, registry=None)
        assert planner._is_chat_message("谢谢") is True
        assert planner._is_chat_message("谢谢你") is True
        assert planner._is_chat_message("你好") is True
        assert planner._is_chat_message("你是谁") is True
        assert planner._is_chat_message("谢谢，辛苦了") is True

    def test_thanks_followed_by_task_is_not_chat(self):
        planner = Planner(llm=None, registry=None)
        assert planner._is_chat_message("谢谢，帮我分析600519") is False

    def test_company_name_query_is_not_chat(self):
        planner = Planner(llm=None, registry=None)
        assert planner._is_chat_message("贵州茅台怎么样") is False
        assert planner._is_chat_message("沪深300走势如何") is False

    def test_long_message_is_not_chat(self):
        planner = Planner(llm=None, registry=None)
        assert planner._is_chat_message("谢谢你的帮助，我接下来想了解新能源行业的整体情况") is False

    def test_plan_chat_message_returns_chat_plan(self, registry, memory):
        planner = Planner(llm=None, registry=registry, memory=memory)
        plan = planner.plan("谢谢")

        assert plan.mode == "chat"
        assert plan.steps == []

    def test_plan_llm_chat_mode_returns_chat_plan(self, registry, memory):
        llm = FakeLLM(fixed_response=make_chat_response())
        planner = Planner(llm=llm, registry=registry, memory=memory)

        plan = planner.plan("随便聊聊")

        assert plan.mode == "chat"
        assert plan.steps == []

    def test_plan_llm_plan_mode_returns_plan(self, registry, memory):
        llm = FakeLLM(fixed_response=make_multi_step_response())
        planner = Planner(llm=llm, registry=registry, memory=memory)

        plan = planner.plan("分析新能源板块")

        assert plan.mode == "plan"
        assert len(plan.steps) == 3

    def test_plan_llm_agent_mode_returns_agent_plan(self, registry, memory):
        llm = FakeLLM(fixed_response=make_agent_response())
        planner = Planner(llm=llm, registry=registry, memory=memory)

        plan = planner.plan("对比茅台和宁德时代")

        assert plan.mode == "agent"
        assert plan.steps == []

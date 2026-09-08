"""Planner — LLM 驱动的任务拆解，只做拆解不绑定工具"""
import json
import logging
import re
from collections.abc import Sequence

from agent.memory import Memory, MemoryMessage, Plan, TaskStep

logger = logging.getLogger(__name__)

SIMPLE_QUERY_PREFIXES = [
    "什么是", "最近", "最新", "当前", "现在", "今天",
    "怎么", "如何", "为什么", "解释",
]

# 纯客套短语（快路径：整条消息由这些短语拼接而成才判定为闲聊；
# 宁可漏判多花一次 LLM 调用，不可误判吞掉业务请求）
CHAT_PHRASES = {
    "谢谢", "谢谢你", "感谢", "辛苦了", "好的", "明白了", "了解",
    "你好", "您好", "嗨", "哈喽", "hello", "hi", "hey",
    "再见", "拜拜", "bye",
    "你是谁", "你能做什么", "你会什么", "介绍一下你自己",
}

_CHAT_PHRASE_PATTERN = re.compile(
    "(?:" + "|".join(re.escape(p) for p in sorted(CHAT_PHRASES, key=len, reverse=True)) + ")+"
)
_PUNCTUATION = "，。！？,．.!?~～、… \t\n"

PLANNER_SYSTEM_PROMPT = """你是一个股票投研任务规划器。你的职责是将用户的投资研究问题拆解为有序的执行步骤。

## 规则
1. 先判断问题复杂度：简单查询（单一信息）→ 1步，复杂研究（多维度）→ 多步
2. 每步只描述"做什么"，不指定"用哪个工具"（工具选择由执行器负责）
3. 标注步骤间的依赖关系
4. 复杂问题步骤数不超过5步，简单问题1步
5. mode 判断：所有投资研究问题（结构化分析、探索式/对比、简单查询）→ agent；
   仅当与投资研究完全无关（问候、道谢、闲聊、非金融话题）→ chat。
   plan 模式为内部保留（复杂多步骤工具编排场景），agent 与 chat 模式 steps 必须为空数组。

## 可用能力概览
{capabilities}

## 输出格式
严格输出 JSON，不要包含其他文字:
```json
{{
  "goal": "用户目标的简洁概括",
  "complexity": "simple|complex",
  "mode": "plan|agent|chat",
  "steps": [
    {{"id": "step-1", "description": "步骤描述"}},
    {{"id": "step-2", "description": "步骤描述", "depends_on": ["step-1"]}}
  ]
}}
```
""".strip()


class Planner:
    """任务规划器 — LLM 驱动的任务拆解"""

    def __init__(self, llm=None, registry=None, memory=None):
        self._llm = llm
        self._registry = registry
        self._memory = memory or Memory()

    def plan(self, user_input: str) -> Plan:
        """根据用户输入生成执行计划"""
        context = self._memory.get_context_window(n=10)
        facts = self._memory.facts

        # 硬编码纯客套快路径 —— 闲聊直接 chat 模式，零成本
        if self._is_chat_message(user_input):
            return Plan(goal=user_input.strip(), steps=[], mode="chat",
                        context_summary="闲聊，普通会话")

        # 硬编码前缀拦截 —— 简单查询免 LLM 规划，直接 agent 自主执行（产出正文）
        if self._is_simple_query(user_input):
            return Plan(goal=user_input.strip(), steps=[], mode="agent",
                        context_summary="简单查询，agent 自主执行")

        # 构建上下文摘要
        context_summary = ""
        if facts:
            fact_lines = [f"  {k}: {v}" for k, v in facts.items()]
            if fact_lines:
                context_summary = "用户偏好:\n" + "\n".join(fact_lines)

        # LLM 规划
        if self._llm is None:
            return self._fallback_plan(user_input, context_summary)

        try:
            system_prompt = self._build_system_prompt()
            prompt = self._build_plan_prompt(user_input, context)
            response = self._llm.generate(prompt, system=system_prompt)

            plan = self._parse_response(response, user_input)
            plan.context_summary = context_summary
            return plan
        except Exception as e:  # noqa: BLE001 — LLM 失败降级为单步计划
            logger.warning(f"Planner LLM 调用失败，使用降级单步计划: {e}")
            return self._fallback_plan(user_input, context_summary)

    def _is_chat_message(self, text: str) -> bool:
        """纯客套快路径：整条消息去掉标点后完全由客套短语拼接而成"""
        stripped = text.strip()
        if len(stripped) > 10:
            return False
        if any(ch.isdigit() for ch in stripped):
            return False
        cleaned = "".join(ch for ch in stripped.lower() if ch not in _PUNCTUATION)
        if not cleaned:
            return False
        return _CHAT_PHRASE_PATTERN.fullmatch(cleaned) is not None

    def _is_simple_query(self, text: str) -> bool:
        """硬编码前缀拦截：匹配前缀 → 直接单步"""
        text_stripped = text.strip()
        if len(text_stripped) > 50:
            return False
        return any(text_stripped.startswith(prefix) for prefix in SIMPLE_QUERY_PREFIXES)

    def _build_system_prompt(self) -> str:
        if self._registry is None:
            capabilities = "无可用工具"
        else:
            capabilities = json.dumps(
                self._registry.list_all_summary(), ensure_ascii=False
            )
        return PLANNER_SYSTEM_PROMPT.format(capabilities=capabilities)

    def _build_plan_prompt(self, user_input: str,
                           context: Sequence[MemoryMessage]) -> str:
        parts = []
        if context:
            recent = context[-4:]
            parts.append("## 最近对话")
            for msg in recent:
                parts.append(f"[{msg['role']}]: {msg['content']}")
        parts.append(f"\n## 当前任务\n{user_input}")
        return "\n".join(parts)

    def _parse_response(self, response: str, fallback_goal: str) -> Plan:
        response = response.strip()
        if "```" in response:
            lines = response.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```"):
                    in_block = not in_block
                    continue
                if in_block:
                    json_lines.append(line)
            if json_lines:
                response = "\n".join(json_lines)

        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            return self._fallback_plan(fallback_goal, "")

        mode = data.get("mode", "plan")
        if mode == "chat":
            return Plan(goal=data.get("goal", fallback_goal), steps=[],
                        mode="chat")
        if mode == "agent":
            return Plan(goal=data.get("goal", fallback_goal), steps=[],
                        mode="agent")

        steps = []
        for s in data.get("steps", []):
            step = TaskStep(
                id=s.get("id", f"step-{len(steps) + 1}"),
                description=s.get("description", ""),
                depends_on=s.get("depends_on", []),
            )
            steps.append(step)

        if not steps:
            return self._fallback_plan(fallback_goal, "")

        return Plan(goal=data.get("goal", fallback_goal), steps=steps)

    def _fallback_plan(self, goal: str, context_summary: str) -> Plan:
        """降级方案：agent 自主循环兜底（模型自主决定工具调用并产出正文）"""
        return Plan(
            goal=goal,
            steps=[],
            mode="agent",
            context_summary=context_summary,
        )

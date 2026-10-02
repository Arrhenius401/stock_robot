# 闲聊回复修复实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 ChatResponder 闲聊回复——从模型响应中正确提取文本（隐藏 thinking 块），并将兜底文案分级为「未配置」与「调用失败」两级。

**Architecture:** 只动 `src/agent/chat.py` 与 `tests/agent/test_chat.py`。新增模块级函数 `_extract_text(content)` 处理 LangChain AIMessage.content 的两种形态（OpenAI 风格 str / Anthropic 风格块列表），`reply()` 内联调用；删除误导性常量 `FALLBACK_REPLY`，替换为 `MODEL_NOT_CONFIGURED_REPLY` 与 `LLM_ERROR_REPLY` 两级兜底。

**Tech Stack:** Python 3.11+、LangChain（ChatAnthropic/ChatOpenAI）、pytest（asyncio）、ruff、pyright。

**规格:** `docs/superpowers/specs/2026-08-18-chat-reply-fix-design.md`

---

## 文件结构

| 文件 | 责任 | 操作 |
|---|---|---|
| `src/agent/chat.py` | ChatResponder + 文本提取 + 兜底常量 | 修改 |
| `tests/agent/test_chat.py` | ChatResponder 单元测试（FakeChatModel 注入） | 修改 |

环境事实（勿重复探测）：pytest 必须用 `.venv/Scripts/python -m pytest`；pyright 直接命令可用；ruff 用 VS Code 扩展 bundled 二进制（通配符路径 `~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe`）。

---

### Task 1: `_extract_text` 文本块提取函数（TDD）

**Files:**
- Modify: `src/agent/chat.py`
- Test: `tests/agent/test_chat.py`

本任务只新增提取函数与测试，不动兜底常量与 `reply()`——任务结束保持全绿。

- [ ] **Step 1: 写失败测试**

`tests/agent/test_chat.py` 顶部导入（第 4 行）改为：

```python
from agent.chat import FALLBACK_REPLY, ChatResponder, _extract_text
```

在 `TestChatResponder` 类之前新增测试类：

```python
class TestExtractText:
    def test_str_passthrough(self):
        assert _extract_text("你好") == "你好"

    def test_list_joins_text_blocks_skips_thinking(self):
        content = [
            {"type": "thinking", "signature": "sig-1", "thinking": "内部思考"},
            {"type": "text", "text": "你好！"},
            {"type": "text", "text": "有什么可以帮你？"},
        ]
        assert _extract_text(content) == "你好！有什么可以帮你？"

    def test_list_ignores_non_dict_blocks(self):
        assert _extract_text(["裸字符串块",
                              {"type": "text", "text": "有效文本"}]) == "有效文本"

    def test_empty_content_returns_empty_string(self):
        assert _extract_text([]) == ""
        assert _extract_text(None) == ""
        assert _extract_text(12345) == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/agent/test_chat.py -q`
Expected: FAIL — `ImportError: cannot import name '_extract_text'`（旧测试 `TestChatResponder` 不受影响）

- [ ] **Step 3: 实现 `_extract_text`**

`src/agent/chat.py` 顶部（第 1-5 行）改为：

```python
"""ChatResponder — 闲聊的普通 AI 会话回复（LangChain 模型，无工具绑定）"""
import logging
from typing import Any

from agent.memory import Memory

logger = logging.getLogger(__name__)
```

（仅新增 `from typing import Any`，`FALLBACK_REPLY` 常量暂保留，Task 2 处理。）

在 `CHAT_SYSTEM_PROMPT` 之后、`ChatResponder` 类之前新增：

```python
def _extract_text(content: Any) -> str:
    """从模型响应 content 提取用户可见文本

    LangChain AIMessage.content 两种形态：OpenAI 风格为 str 原样返回；
    Anthropic 风格为内容块列表（含 thinking 块），只拼接 type == "text"
    的块文本，thinking/signature 不展示给用户。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/agent/test_chat.py -q`
Expected: PASS — 10 passed（4 个新提取测试 + 6 个旧测试）

- [ ] **Step 5: 提交**

```bash
git add src/agent/chat.py tests/agent/test_chat.py
git commit -m "feat(Agent): 闲聊回复文本提取 — 隐藏 thinking 块"
```

---

### Task 2: 兜底文案分级与 reply() 收尾（TDD）

**Files:**
- Modify: `src/agent/chat.py`（常量 + `reply()` 方法）
- Test: `tests/agent/test_chat.py`

- [ ] **Step 1: 写失败测试 + 新增常量**

`tests/agent/test_chat.py` 顶部导入改为（类型序：常量→类→函数；行宽超 88 时 ruff 默认垂直换行，每项一行）：

```python
from agent.chat import (
    LLM_ERROR_REPLY,
    MODEL_NOT_CONFIGURED_REPLY,
    ChatResponder,
    _extract_text,
)
```

（`FALLBACK_REPLY` 移出导入——Task 2 Step 3 将删除该常量。）

把 `TestChatResponder` 中最后两个测试（`test_no_model_returns_fallback`、`test_model_exception_returns_fallback`）替换为：

```python
    @pytest.mark.asyncio
    async def test_no_model_returns_not_configured(self):
        responder = ChatResponder(model=None)

        reply = await responder.reply("你好", Memory())

        assert reply == MODEL_NOT_CONFIGURED_REPLY

    @pytest.mark.asyncio
    async def test_model_exception_returns_error_reply_with_reason(self):
        class ExplodingModel(FakeChatModel):
            async def ainvoke(self, messages, **kwargs):
                raise RuntimeError("API 不可用")

        responder = ChatResponder(model=ExplodingModel())

        reply = await responder.reply("你好", Memory())

        assert reply == LLM_ERROR_REPLY.format(error="API 不可用")
```

在 `TestChatResponder` 末尾追加两个测试：

```python
    @pytest.mark.asyncio
    async def test_reply_extracts_text_blocks_from_list_content(self):
        """DeepSeek 端点返回 thinking+text 块列表，只取 text 且不含内部思考"""
        model = FakeChatModel(content=[
            {"type": "thinking", "signature": "sig-1", "thinking": "内部思考"},
            {"type": "text", "text": "你好！很高兴见到你。"},
        ])
        responder = ChatResponder(model=model)

        reply = await responder.reply("你好", Memory())

        assert reply == "你好！很高兴见到你。"
        assert "thinking" not in reply and "sig-1" not in reply

    @pytest.mark.asyncio
    async def test_reply_empty_content_returns_error_reply(self):
        model = FakeChatModel(content=[])
        responder = ChatResponder(model=model)

        reply = await responder.reply("你好", Memory())

        assert reply == LLM_ERROR_REPLY.format(error="模型未返回有效回复")
```

`src/agent/chat.py` 常量区（原 `FALLBACK_REPLY` 位置）新增：

```python
MODEL_NOT_CONFIGURED_REPLY = "AI 对话未启用：请在配置中设置 llm.api_key"
LLM_ERROR_REPLY = "（AI 回复暂时不可用：{error}，请检查 API 配置）"
```

（`FALLBACK_REPLY` 暂保留——`reply()` 仍在引用，Step 3 一并删除，避免中途 NameError。）

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/agent/test_chat.py -q`
Expected: FAIL — 4 个新增/改动的测试失败（断言不符：当前 `reply()` 返回旧 `FALLBACK_REPLY` 字符串或 `str(list)` 原始 repr），其余 6 个旧测试通过

- [ ] **Step 3: 实现 `reply()` 分级兜底并删除旧常量**

删除 `src/agent/chat.py` 中的 `FALLBACK_REPLY` 常量定义，把 `ChatResponder.reply` 整体替换为：

```python
    async def reply(self, user_input: str, memory: Memory) -> str:
        if self._model is None:
            return MODEL_NOT_CONFIGURED_REPLY
        try:
            messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
            for msg in memory.get_context_window(n=10):
                if msg["role"] not in ("user", "assistant"):
                    continue
                messages.append({"role": msg["role"], "content": msg["content"]})
            # API 调用方已把当前用户消息写入 memory，避免上下文重复
            last = messages[-1] if messages else None
            if last is None or last.get("content") != user_input:
                messages.append({"role": "user", "content": user_input})

            response = await self._model.ainvoke(messages)
            content = _extract_text(getattr(response, "content", ""))
            if not content:
                return LLM_ERROR_REPLY.format(error="模型未返回有效回复")
            return content
        except Exception as e:  # noqa: BLE001 — LLM 边界异常降级可诊断提示
            logger.error("闲聊回复失败: %s", e)
            return LLM_ERROR_REPLY.format(error=e)
```

同时把类 docstring 从「失败降级为固定提示」改为「失败降级为可诊断提示」。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/agent/test_chat.py -q`
Expected: PASS — 12 passed（6 旧 + 2 改写 + 4 新增提取/回复测试）

- [ ] **Step 5: 单文件检查**

Run: `pyright src/agent/chat.py tests/agent/test_chat.py`
Expected: 0 errors

Run: `~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe check src/agent/chat.py tests/agent/test_chat.py`
Expected: 0 errors（`reply()` 的 `except Exception` 是 LLM 边界，带 `# noqa: BLE001` 与改动前一致）

- [ ] **Step 6: 提交**

```bash
git add src/agent/chat.py tests/agent/test_chat.py
git commit -m "fix(Agent): 闲聊兜底分级 — 未配置/调用失败可诊断文案"
```

---

## 验证（全部任务完成后）

```bash
.venv/Scripts/python -m pytest tests/agent/test_chat.py -q
```

Expected: 12 passed

冒烟验证（可选，真实 API，需要有效 `llm.api_key`）：

```bash
PYTHONPATH=src .venv/Scripts/python -c "import asyncio; from agent.chat import ChatResponder; from agent.memory import Memory; from agent.model_factory import create_chat_model; from utils.config import Config; async def m(): r = await ChatResponder(model=create_chat_model(Config())).reply('你好', Memory()); print(repr(r)); assert 'thinking' not in r; asyncio.run(m())"
```

Expected: 打印干净的中文回复（如「你好！很高兴见到你。有什么我可以帮你的吗？」），不含 thinking JSON

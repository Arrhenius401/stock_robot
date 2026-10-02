# 闲聊回复修复设计 — 文本提取与兜底分级

- 日期: 2026-08-18
- 状态: 已确认
- 相关代码: `src/agent/chat.py`、`tests/agent/test_chat.py`

## 背景与动机

- ChatResponder 闲聊链路（CLI/API 的 `plan.mode == "chat"` 分支）本应调用 LangChain 模型返回普通 AI 回复，但实际只返回固定拒答文案「这个问题我暂时无法回答。可以试试让我分析某只股票」。
- 排障确认两层根因：
  1. 配置 `~/.stock_robot/config.yaml` 的 DeepSeek api_key 无效（OpenAI 兼容端点与 `/anthropic` 端点均 401）——用户已自行更换 key 修复。
  2. key 有效后模型可调通，但 DeepSeek 端点默认返回 Anthropic 风格 content 块列表 `[{type: thinking, ...}, {type: text, text: ...}]`，`ChatResponder.reply` 用 `str(content)` 直接转换，把含思考块的原始 repr 作为回复（`chat.py:36-38`）。
- 连带问题：固定兜底文案有误导性。LLM 失败（401/网络）时用户看到的是「AI 不会回答」，而建议的替代方案（分析股票）同样依赖 LLM 也会失败——本次排障即被此文案误导。项目自有 `ClaudeAdapter.generate`（`src/llm/claude.py:50-52`）已有正确的块级文本提取先例，chat 路径未对齐。

## 决策要点

| 决策 | 选择 | 理由 |
|---|---|---|
| 修复范围 | 文本提取 + 兜底分级，不动闲聊判定（`planner._is_chat_message`） | 用户当前诉求是「普通 AI 回答」；判定扩大另议 |
| 提取逻辑位置 | `chat.py` 模块级私有函数 `_extract_text` | 目前唯一消费方是 ChatResponder，YAGNI 不提前抽象 |
| 模型路径 | 保留 LangChain 模型（`core.model`），不复用 LLMBackend | agent 层统一走 LangChain 是既定方向；LLMBackend 无多轮消息接口 |
| 兜底文案 | 分级：未配置 / 调用失败 | 对齐 `ClaudeAdapter` 现有错误文案风格 |

## 改动明细

### `src/agent/chat.py`

新增模块级函数 `_extract_text(content) -> str`：

```
content 为 str        → 原样返回（OpenAI 风格）
content 为 list       → 遍历块，dict 且 type == "text" 时拼接其 text 字段
                        （跳过 thinking/signature 块）
其他 / 提取结果为空串  → 返回 ""（调用方走兜底）
```

常量调整：

- 删除 `FALLBACK_REPLY`（「这个问题我暂时无法回答…」误导文案）
- 新增 `MODEL_NOT_CONFIGURED_REPLY = "AI 对话未启用：请在配置中设置 llm.api_key"`
- 新增 `LLM_ERROR_REPLY = "（AI 回复暂时不可用：{error}，请检查 API 配置）"`（格式对齐 `ClaudeAdapter.generate` 的降级文案）

`reply()` 改造：

| 场景 | 行为 |
|---|---|
| `self._model is None` | 返回 `MODEL_NOT_CONFIGURED_REPLY` |
| `ainvoke` 抛异常 | `logger.error` 记录后返回 `LLM_ERROR_REPLY.format(error=e)` |
| `_extract_text` 结果为空串 | 返回 `LLM_ERROR_REPLY.format(error="模型未返回有效回复")` |
| 正常 | 返回提取后的干净文本 |

异常路径保留 `# noqa: BLE001`（LLM SDK 边界，异常类型不可预测）。

### `tests/agent/test_chat.py` 扩展

- content 为 `[thinking 块, text 块]` → 只返回 text 块内容，thinking 不出现
- content 为 str → 原样返回
- content 为空 list / 空串 → 走 LLM_ERROR_REPLY
- ainvoke 抛异常 → 返回含异常信息的 LLM_ERROR_REPLY
- model 为 None → 返回 MODEL_NOT_CONFIGURED_REPLY

## 边界说明（不改）

- 兜底文案照常写入 memory 的 assistant 消息（现状行为保留——让用户知道 AI 服务出过问题）
- 执行器 / ToolSelector 不受影响：只读 `response.tool_calls`，不走 content 文本
- 闲聊判定（`planner._is_chat_message` 硬编码纯客套快路径）不在本次范围
- 历史会话中已存储的原始 repr 消息不迁移，随轮次自然淘汰

## 验收标准

- CLI/API 闲聊（如「你好」）返回干净 AI 回复，无 thinking JSON 泄漏
- 断网/错误 key 场景返回「（AI 回复暂时不可用：…）」而非误导性拒答
- 未配置 key 场景返回「AI 对话未启用」提示
- `ruff check src/agent/chat.py tests/agent/test_chat.py` 与 `pyright` 单文件检查通过，`pytest tests/agent/test_chat.py -q` 全绿

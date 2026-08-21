# 项目优化点待办清单

> 审查日期：2026-08-20
> 审查范围：src/agent、src/api、src/core、src/report、src/data、src/utils、cli.py、src/stock_robot/cli.py（约 9500 行）
> 状态：已记录，未开始处理。每个优化点后续走 spec → plan → 实现流程时，在对应文档中链接回此处。

## 高价值

| # | 优化点 | 位置 | 说明 |
|---|--------|------|------|
| H1 | 股票代码表无缓存 | src/utils/symbols.py:12-15 | `_ak_code_name()` 每次调用 `ak.stock_info_a_code_name()` 下载全量代码表，无任何缓存；API 的 analyze 每请求调用一次 `resolve_name`（src/api/app.py:231），CLI 同理。建议日级 TTL 缓存，可复用 src/data/cache.py 或进程级缓存 |
| H2 | API 安全防护缺失 | src/api/app.py:71-72, 146-149, 268-270 | CORS `allow_origins=["*"]`；500 错误将 `str(e)` 直接返回客户端泄漏内部细节；chat/analyze 触发付费 LLM 调用，绑 0.0.0.0 时零防护。建议默认 localhost 绑定 + 简单 token 鉴权 + 错误脱敏 |
| H3 | 死代码 | src/core/pipeline.py:34, 69, 151 | `DIMENSION_LLM_LABELS`（:34）、`SCHEMA_CLASS_MAP`（:69）全仓库无引用；`collect_from_target`（:151）仅测试引用；src/agent/graph.py:29-31 `plan_to_state` 的 `session_id` 参数未使用（"预留"注释已过期）。建议删除 |
| H4 | CLI 与 bootstrap 重复组装 | cli.py:21-58 vs src/api/bootstrap.py:73-130 | `_get_registry`/`_register_llm`/`_build_pipeline` 与 `build_agent_core` 重复注册同一套数据源+分析模块+LLM，改一处漏一处。建议 CLI analyze 复用 bootstrap |
| H5 | 重复代码 | cli.py:194-261, pipeline.py:245-250 | `_render_index_report` 与 `_render_index_report_md` 约 35 行几乎全同（各建一次 Jinja env）；pipeline.py 内联 `dim_labels`/`sufficiency_label` 与 src/report/scoring.py 的 `DIM_LABELS`/`SUFFICIENCY_LABEL` 完全同值。建议抽公共函数、复用 scoring 常量 |

## 中价值

| # | 优化点 | 位置 | 说明 |
|---|--------|------|------|
| M1 | 跨类访问私有成员 | src/core/pipeline.py:144, src/agent/pipeline_tools.py:312 | 直接调 `_config_loader._load_yaml(...)`、遍历 `classifier._mapping`，绕过公共接口。建议给 ConfigLoader/IndustryClassifier 补公共方法 |
| M2 | SQLite 每操作新建连接 | src/data/cache.py:21, src/api/sessions.py:19 | 每次 get/put 都 `sqlite3.connect`，靠 GC 关连接；API 并发下反复建连。建议复用单连接（加锁）或连接池 |
| M3 | analyze 热路径重复磁盘 IO | src/api/app.py:60, 297, src/agent/pipeline_tools.py:200 | `_build_signal_payload` 每次请求新建 `Config()` 读 config.yaml；`IndexMapping()` 每次重读 CSV。建议进程级缓存 |
| M4 | 无效 CLI 桩命令 | cli.py:562, 578-580 | `/tools` 打印"暂时不可用"；`/verbose` 只打印"已切换"不改变任何行为。建议实现或移除 |
| M5 | chat 重复消息判断脆弱 | src/agent/chat.py:53-55 | 用 `last.get("content") != user_input` 判重，用户连续发两条相同消息时第二条被跳过。建议改由调用方保证不重复写入 |

## 低价值

| # | 优化点 | 位置 | 说明 |
|---|--------|------|------|
| L1 | UsageLogger 无直接测试 | src/llm/usage.py | tests/llm 缺 test_usage.py，日志格式/成本估算未验证 |
| L2 | 函数内重复 import | src/utils/symbols.py:73, 89 | 函数内 `import re` 重复（模块顶部已导入） |
| L3 | CLAUDE.md 项目结构图过时 | CLAUDE.md | 仍写根目录 cli.py，实际已移至 src/stock_robot/cli.py |

## 测试覆盖结论

src/agent、api、core、report、index、rag、mcp 均有对应测试且较厚（如 app.py 632 行测试、graph 225 行），无大面积盲区；唯一明确缺口是 UsageLogger（L1）。

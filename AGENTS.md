# AGENTS.md

## 项目事实

Stock Robot：AI 股票研报助手；确定性代码采集数据、计算指标，LLM 负责自然语言解读。
架构：CLI → 数据层 → 分析层 → LLM → 报告构建 → 输出，各层通过抽象基类解耦。
Python 3.11+，Pydantic v2、AkShare、pandas、SQLite、Jinja2、OpenAI/Anthropic SDK。

- 注释、文档和提交信息使用中文；提交格式如 `fix(缓存): 修复过期判断`。
- Python 命令必须显式用 `.venv/Scripts/python`，pytest 用 `.venv/Scripts/python -m pytest`；裸 Python 指向 Anaconda。
- 首次同步用 `.\scripts\bootstrap-dev.ps1`；开发环境为 `.[dev,rag]`。业务核心不含 chromadb、sentence-transformers；依赖变化须重新导出 core/rag 锁定清单并验证。

## 定位代码

存在 `.codegraph/` 时，定位或理解代码先使用 CodeGraph；未建立索引则跳过。
工具未显示时先搜索 `codegraph_explore`，传入 `projectPath: "D:\\code\\stock_robot"` 和具体文件、符号或问题。
仅在 `Get-Command codegraph` 确认可用时使用 CLI 后备；不能因 MCP 未直接显示就认定不可用。

## 核心流程与底线

- 遵守全局协作、文档与提交约定；当前用户明确要求优先。
- 新功能、业务变化、跨模块重构和明显 UI 改动使用 `design-before-implementation`；先确认完整方案，UI 另确认预览。已确认计划与明确小修复直接执行。
- 正式文档操作使用 `document-lifecycle`：执行中缺陷、阶段和验收归属原项目；独立小修复不另建设计/计划。技能不可用时仍遵守这些底线。
- 设计放 `docs/superpowers/specs/<年月>/`，计划放 `docs/superpowers/plans/<年月>/`；阶段不另拆文件，完整研究及临时产物放忽略目录 `tmp/`。
- 开发、修复及提示词/模板修改使用 `test-discipline`；先读取下表中所有适用的项目细则。
- 本仓库默认只授权本地提交；推送、合并、发布及改写既有历史按用户授权执行。
- 关键决定和验证在聊天及提交中说明，不为每一步新建正式文档。

## 按任务必读

以下是项目约束的维护来源。开始相关操作前读取对应文件；一项任务命中多行时都要读取，不能只读其中一份。

| 任务 | 必须读取 |
|---|---|
| 修改 Python 源码或测试 | [Python 编码约定](.agents/references/python.md) |
| 运行检查/测试、修改测试资源、依赖/配置变化或提交 | [验证与提交检查](.agents/references/verification.md) |
| 新功能、业务变化、跨模块/高风险改动、复杂任务实施或安排审查 | [实施与审查约定](.agents/references/workflow.md) |
| 前端、页面布局、会话、SSE 或历史消息解析 | [Web 与会话验收](.agents/references/web.md) |
| 配置雷达、回测曲线或相关详情页 | [配置雷达与图表约定](.agents/references/radar.md)；涉及前端时还读 Web 约定 |

## 验证入口

- 日常：`.\scripts\verify.ps1 -Scope Changed`；提交前用 `-Source Staged`；可加 `-PlanOnly` 查看计划。
- 默认按改动范围检查，不自动回退全量；未映射范围用 `-Files` 明确指定，不能将未覆盖范围表述为通过。
- 全量只在合并/发布前或用户明确要求时运行：`.\scripts\verify.ps1 -Scope Full`。
- 仅文档、忽略规则或 preview 清理：`git diff --cached --check`，不重跑业务测试。
- Ruff、Pyright 配置在 `pyproject.toml`；不随意扩大 Ruff 规则集，不依赖 IDE 连接作为复查门禁。
- 前端/会话/SSE 必须以 `./.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765` 启动真实服务，在内置浏览器真实发送并验证响应，不能只凭 Mock 或截图交付。
- 测试每次使用项目内唯一临时目录，保留失败信息；清理错误不推翻已通过检查、不阻断无关提交，细则见验证约定。

## 维护规则

新增约定先判断能否交给现有配置/脚本，或归入上述唯一维护来源。
根 AGENTS 只保留项目必要事实、关键底线及读取入口；不把历史故障、逐项编码建议或任务进度追加回来。
全局技能不可用时按本文件底线执行，并说明无法加载的细则；不静默声称完整遵守。

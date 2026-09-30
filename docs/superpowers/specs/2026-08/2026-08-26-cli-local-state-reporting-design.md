# CLI、本地状态与报告输出优化设计

## 目标

优化启动 Web UI 的命令体验，并让本地运行状态和持久化报告具有稳定、可发现的项目内目录结构。

## 范围与兼容性

- `stock-robot api` 被彻底移除；唯一的 Web UI 启动命令为 `stock-robot run`。
- `run` 保持原有的 `--host`、`--port` 参数和 API 配置优先级。
- `analyze` 与 `index` 的分析语义不变。`index` 仅在 `--output markdown` 时写入报告文件，保留默认终端输出行为。
- 不迁移用户目录下既有的 `~/.stock_robot` 数据；本次仅定义后续运行的数据位置。

## CLI 启动反馈

`run` 以 Rich `Progress` 展示三个阶段：构建 Agent 核心、创建 Web 应用、启动 HTTP 服务。Uvicorn 在后台线程运行，主线程等待其 `started` 状态；确认监听后进度条完成，输出访问 URL，并继续等待服务线程。启动过程中出现异常时，命令显示错误并以非零状态退出。

## 项目内本地状态

`Config.config_dir` 统一使用 `Path.cwd() / ".stock_robot"`。因此配置、SQLite 缓存、RAG 持久化目录、Agent 记忆与检查点、LLM 使用日志都在执行命令时的当前目录下。`.gitignore` 加入 `/.stock_robot/`。

## 指标本地化

在报告格式化层维护一个显式的英文键到中文显示名映射。报告表格在写入前经过该映射，确保股票报告与指数报告共用规则。`PE`、`PB`、`ROE` 等公认缩写作为映射值保留原写法。未在映射表中的指标键原样输出，方便后续发现并增补规范映射。

## 报告持久化与同步

报告保存位置固定为：

```text
reports/<指数或股票代码>/<YYYY-MM>/<代码>_<YYYYMMDD>_<HHMMSS>.md
```

报告格式化器负责创建父目录并返回实际文件路径。`LocalReportSync` 改用递归 Markdown 扫描，以便将该层次下的报告继续摄入 `history_reports`。

## 测试与文档

先编写并验证失败的测试，再实现：CLI 命令公开接口与启动反馈、本地状态目录、指标映射、报告层次路径和递归同步。README 更新启动命令、本地状态路径、报告示例及 RAG 摄入示例。

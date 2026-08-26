# Task 1 实现报告：统一 CLI 进度条

## 改动摘要

- 新增 `stock_robot.cli._create_cli_progress()`，统一返回三列 Rich 进度条：Spinner、阶段描述和横向进度条。
- 统一设置 `transient=True`，任务结束后自动清除进度条；移除完成计数列，不显示 `completed/total`。
- `analyze`、`index`、`run` 三条命令改为复用该工厂。
- 保留 `analyze` 与 `index` 原有 `on_progress(stage, current, total, label)` 回调及内部进度更新逻辑。
- 增加工厂配置测试，以及 analyze/index 触发进度回调的回归测试。

## 验证

命令：

```powershell
D:\code\stock_robot\.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_cli_index.py -q
```

输出：`26 passed in 1.07s`

命令：

```powershell
& "$env:USERPROFILE\.vscode\extensions\charliermarsh.ruff-*\bundled\libs\bin\ruff.exe" check src/stock_robot/cli.py tests/test_cli.py tests/test_cli_index.py
```

输出：`All checks passed!`

## 提交

提交信息：`refactor(命令行): 统一分析进度条样式`

提交哈希：`b930cb9`

## 疑虑

- 当前 Rich 版本将 `transient` 保存在 `Progress.live.transient`，而不是 `Progress.transient`；测试因此检查实际公开的 Live 配置。
- 测试环境默认临时目录权限不足，本次使用项目内临时目录运行 pytest；该环境因素不影响代码行为。

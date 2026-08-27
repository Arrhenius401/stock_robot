# Task 6 实施简报：文档与真实路径验收

## 范围

- 更新 `README.md`，说明设置页、配置文件与状态目录位置、host/port 修改需重启、凭据遮罩仅适用于本地可信环境，以及未发送消息的新会话不会持久化。
- 在 `tests/api/test_static.py` 校验静态资源清单包含 `/js/settings.js`。
- 按计划运行针对性测试、真实 `.venv/Scripts/stock-robot.exe run` 启动验证，以及 Ruff、pyright、全量 pytest，并如实记录环境限制。

## 不变量

- 不修改业务逻辑；文档与当前实现和 CLI 命令一致（仅保留 `stock-robot run`）。
- 验收失败或受环境限制时记录实际输出，不以静态测试替代真实启动路径检查。

## 交付

- 提交 `docs(使用): 说明会话与配置管理`。
- 在 `task-6-report.md` 记录命令、结果和真实启动验证情况。

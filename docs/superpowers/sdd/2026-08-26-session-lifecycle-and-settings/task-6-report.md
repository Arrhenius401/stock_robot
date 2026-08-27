# Task 6 报告：文档与真实路径验收

## 变更

- 更新 `README.md`：说明 `stock-robot run`、项目当前位置的 `.stock_robot/` 配置与状态目录、配置页面、敏感凭据遮罩、本机可信访问限制、host/port 修改需重启，以及未发送消息的新会话不持久化。
- 更新 `tests/api/test_static.py`：静态资源清单增加 `/js/settings.js`。

## 验证

### 针对性回归

命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_cli.py tests/test_cli_index.py tests/api/test_sessions.py tests/api/test_app.py tests/api/test_configuration.py tests/api/test_static.py -q
```

结果：`230 passed, 5 warnings`。

### 真实启动路径

命令：`.venv/Scripts/stock-robot.exe run`

该进程被拉起并保持运行，但未确认服务监听；本次受执行环境限制没有产生可读的启动输出，访问 `http://127.0.0.1:25618/` 返回“由于目标计算机积极拒绝，无法连接”。随后已停止该验证进程。未将静态测试结果冒充为浏览器验收结果。

### 提交前检查

- Ruff：未通过，发现仓库既有的 `scripts/rebuild_industry_mapping_runner.py` 导入排序/BLE001，以及 `src/api/message_content.py` 导入排序问题；本任务未修改这些文件。
- pyright：未通过，4 个既有错误，位于 `src/agent/planner.py`、`tests/api/test_app.py`、`tests/api/test_sessions.py`；本任务未修改这些文件。
- 全量 pytest：已启动，但运行超过本次验收窗口且无新增输出，已中止；因此不宣称全量通过。

# 报告保留与主仓自清理实施计划

- 日期：2026-10-31
- 设计：[报告保留与主仓自清理设计](../../specs/2026-10/2026-10-31-report-retention-maintenance.md)
- 状态：代码与测试完成；发布准备（版本、CHANGELOG、标签、插件绑定）待单独执行

## 任务与结果

| 任务 | 状态 | 结果 |
| --- | --- | --- |
| 主仓配置新增 `maintenance` 段与校验 | 完成 | `utils/config.py`、`api/configuration.py`；维护字段不触发运行时重载 |
| 报告识别与删除 | 完成 | `src/maintenance/report_retention.py`；保留期固定 `RETENTION_DAYS = 7`，文件名/月份/快照摘要校验、孤立快照回收、空目录清理 |
| 周期任务与跨进程锁 | 完成 | `src/maintenance/runner.py`；启动即扫描、默认 360 分钟、`filelock` 独占 |
| 状态与手动清理接口 | 完成 | `GET /api/v1/maintenance/status`、`POST /api/v1/maintenance/sweep`（仅本机） |
| Web 配置页字段 | 完成 | “数据与缓存”卡片仅新增清理开关；静态资源版本升至 `20261031-report-maintenance-1` |
| 启动接线 | 完成 | `run` 构造 runner，lifespan 启停线程；测试与模块级 app 不启动清理 |
| 插件删除旧清理实现 | 完成 | 移除 `clean_raw`、`FORMATTER_SHA256`、归属门禁与 `_cleanup_uncertain` |
| 插件双向同步 | 完成 | `retention_sync.py`；基线 + 冲突以服务端为准；外部服务不读不改 |
| 两种仓库文档 | 完成 | 主仓 README/使用指南/设计/计划；插件 README/维护与排障/schema 提示 |

## 验证

### 主仓

```bash
.\.venv\Scripts\python.exe -m ruff check src/maintenance src/api tests/maintenance tests/api/test_maintenance.py tests/api/test_configuration.py tests/api/test_static.py
.\.venv\Scripts\pyright.exe src/maintenance src/api/maintenance.py src/api/configuration.py src/utils/config.py tests/maintenance tests/api/test_maintenance.py tests/api/test_configuration.py
.\.venv\Scripts\python.exe -m pytest tests/maintenance tests/api tests/test_cli.py tests/utils -q
```

结果：Ruff 通过；Pyright `0 errors`；pytest **587 passed**（69 秒）。首次运行时唯一失败是静态验收仍断言旧的 `app.js` 资源版本，已更新断言并复跑通过；保留期改为固定 7 天后复跑同一范围通过。

真实启动验收（`stock-robot run --host 127.0.0.1 --port 25692`，工作目录为隔离副本）：

- 启动后立即清理：10 天前报告被删除，5 天前报告保留（固定 7 天边界）；`last_run` 为 `state=ok, scanned=2, deleted=1, failures=0`。
- `GET /api/v1/maintenance/status` 返回 `{report_cleanup_enabled, sweep_interval_minutes}` 与最近结果；`POST /api/v1/maintenance/sweep` 手动执行成功。
- `PUT /api/v1/config` 关闭清理：`applied=false`、`restart_required=false`，下一次手动清理返回 `state=disabled`；`report_retention_days` 更新返回 422（该配置项不存在）。
- 停止服务后端口不再监听，无残留进程。

### 插件

```bash
.\.venv\Scripts\python.exe -m ruff check main.py launcher.py report_storage.py retention_sync.py tests
.\.venv\Scripts\python.exe -m pytest tests -q
```

结果：Ruff 通过；pytest **338 passed, 1 skipped**（45 秒，跳过项为原有的离线安装冒烟）。新增 `tests/test_retention_sync.py` 覆盖首次迁移、插件修改、服务端修改、键删除、锁占用、非法配置与损坏基线。

## 未覆盖与后续

- 未在真实浏览器中打开配置页核对新增字段的实际渲染与保存；本轮以静态 DOM 验收（`test_static.py` 断言清理开关存在、保留天数输入框不存在）和真实 API 往返代替。
- 未重复插件真实 AstrBot 管理页、QQ/WebChat 收图与自动安装链路；本次只改动清理与配置同步，未改分析、渲染和交付。
- 未执行主仓 `scripts/verify.ps1 -Scope Full` 全量门禁（合并/发布前必须执行）。
- 发布准备未做：主仓 `VERSION`/CHANGELOG 与 `v0.3.2` 标签、插件 `metadata.yaml`/CHANGELOG 与 `MANAGED_RELEASE_TAG` 绑定。顺序必须是先发布主仓版本、再把插件绑定到该版本，否则插件会绑定到不含 `maintenance` 的旧服务。

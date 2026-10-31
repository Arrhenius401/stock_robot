# 报告保留与主仓自清理设计

- 日期：2026-10-31
- 状态：已实施（验证见[实施计划](../../plans/2026-10/2026-10-31-report-retention-maintenance.md)）
- 涉及仓库：stock_robot（主仓）、astrbot_plugin_stock_robot（插件）

## 背景与目标

报告清理原先在插件侧实现：插件在持有自建服务进程、且 `formatter.py` SHA-256 与绑定版本一致时，删除超过 7 天、没有结构快照的股票/指数 Markdown。该实现有三个问题：

1. 只删没有 `.report.json` 的报告；插件路径生成的报告都带快照，实际几乎不删。
2. 依赖报告保存实现的哈希，升级主仓就要同步修改插件，且未知布局一律跳过。
3. 服务若同时供 Web、推送或其他客户端使用，插件无法证明安全，只能要求用户关闭清理。

目标：由主仓认识并清理自己的报告产物；插件保留同名开关，并与主仓配置保持同步。

## 已确认决策

| 决策 | 选择 |
| --- | --- |
| 同步策略 | A：双向同步 + 基线；冲突时以服务端为准 |
| 清理范围 | 仅个股、指数、黄金研报（含结构快照）；不回测、雷达产物、日志、数据库 |
| 保留期 | 固定 7 天，不提供按天数调整的配置项；清理默认开启，可在 Web 配置页关闭 |

## 主仓设计

### 模块

- `src/maintenance/report_retention.py`：识别报告单元并按保留期删除。
- `src/maintenance/runner.py`：后台线程、跨进程锁、状态记录。
- `src/api/maintenance.py`：`GET /api/v1/maintenance/status`、`POST /api/v1/maintenance/sweep`（仅本机）。
- `src/api/app.py`：仅在显式注入 `maintenance` 时用 lifespan 启停线程；测试与模块级 app 不启动清理。
- `src/stock_robot/cli.py`：`run` 命令用 `Path.cwd() / "reports"` 和 `Config().config_dir` 构造 runner。

### 配置

```yaml
maintenance:
  report_cleanup_enabled: true
  sweep_interval_minutes: 360
```

两项都进入 `_EDITABLE_FIELDS`，扫描间隔范围为 1～1440 分钟。保留期由 `report_retention.RETENTION_DAYS = 7` 固定，旧配置中残留的 `report_retention_days` 不会生效。维护设置不属于热重载字段：保存后由清理循环下一轮读取，不重建 Agent 核心；与 llm 等热字段一起保存时仍按热字段重载。

### 识别与删除规则

- 只处理 `reports/{stock,index,gold}/<标的>/<YYYY-MM>/` 与旧版根目录 `<六位代码或五位.HK>/<YYYY-MM>/`。
- 文件名必须为 `<前缀>_<YYYYMMDD>_<HHMMSS>[_<32 位十六进制>].md`，前缀与标的目录一致，日期月份与月份目录一致；黄金只接受 `GOLD`。
- 到期时间取文件名时间戳（本地时区），解析失败跳过；固定保留 7 天，`超过` 7 天才删除，恰好 7 天保留。
- 有快照时校验 `schema_version == 1` 且 `markdown_sha256` 与原文一致，失败整组保留；目录中的未知文件不删除。
- 删除顺序为快照→原文；只删一半时下一轮按孤立快照回收。删除后仅移除空月份/标的目录。
- 链接、重解析点、越界路径一律跳过；每轮使用 `filelock` 的 `maintenance.lock`，拿不到锁记为 `locked` 并跳过。

### 调度

`run` 启动即扫描一次，之后每 `sweep_interval_minutes` 扫描；单轮异常只记录日志，不终止后台线程。

## 插件设计

- 删除 `ReportStorage.clean_raw`、`can_clean_raw`、`cleanup_raw`、`FORMATTER_SHA256` 及 `_cleanup_uncertain` 门禁，插件不再解析服务报告布局。
- 保留 `cleanup_previous_reports`（默认 `true`），说明改为“由分析服务执行，与 Web 配置页同步”。
- 新增 `retention_sync.py`：在 `ServiceLauncher._configure`（服务启动前、配置已就绪）读写 `instance/.stock_robot/config.yaml` 的 `maintenance.report_cleanup_enabled`。
- 同步基线存于插件数据目录 `retention-sync.json`（值 + `config.yaml` 的 `mtime_ns`）：
  - 无基线：服务端有显式值则以其为准回写插件；否则把插件值写入服务端。
  - 基线存在且配置文件未被外部改动：插件值变化则写入服务端。
  - 配置文件被外部改动：以服务端值为准回写插件；键被删除视为恢复默认 `true`。
  - 两边同时改动且不同：服务端优先并记录日志。
- 写配置使用同目录临时文件（`mkstemp`，0600）+ `os.replace`，保留其他配置键；拿不到 `config.yaml.lock` 时跳过。
- 复用外部/健康服务时不读取、不修改其配置（沿用既有设计原则）。
- 绑定版本需在主仓发布后升级；旧版服务没有 `maintenance` 字段，插件只写入该键且不报错，清理由服务版本决定。

## 不做与边界

- 不清理回测、雷达、`runtime-*.log`、SQLite、缓存、依赖环境和用户另存副本。
- 不提供容量淘汰；固定 7 天保留期是唯一规则。
- 删除后聊天中的旧报告链接失效，属于预期行为。
- 插件在服务运行时（Web 修改）不即时同步 AstrBot 页面，下一次插件重载时镜像服务端值。

## 风险

| 风险 | 缓解 |
| --- | --- |
| 行为变化：以前带快照的报告不会删 | CHANGELOG 与 README 明示整组删除；固定 7 天与现有文案一致 |
| 两个写入者修改同一配置 | 仅在服务停止时写、使用同一锁文件、基线区分变更来源 |
| 删除正在被读取的报告 | Windows 删除失败留待下一轮；Linux 已读入内存 |
| 旧服务版本 | 插件写未知键不报错；服务升级后生效 |

# 股票资料手动任务

股票任务与ETF更新分别保存。自动更新保持关闭，启动CLI不会注册定时任务。当前可执行官方名录候选发现，以及既有成员的原始日线和股本采集。

## 命令

输入JSON示例：

```json
{"kind":"discover_candidates","pool_ids":["dividend_value","growth_trend"],"target_date":"2026-10-16","rule_version":"unconfigured"}
```

在实施工作树中运行，复用已有环境：

```powershell
$env:PYTHONPATH="$PWD/src;$PWD"
& D:/code/stock_robot/.venv/Scripts/stock-robot.exe radar stock-update-enqueue --db tmp/research.db --input tmp/request.json
& D:/code/stock_robot/.venv/Scripts/stock-robot.exe radar stock-update-run-one --db tmp/research.db --cache-dir tmp/updates/cache --artifact-dir tmp/updates/artifacts --output tmp/update-result.json
& D:/code/stock_robot/.venv/Scripts/stock-robot.exe radar stock-update-list --db tmp/research.db
```

研究库须先存在；股票池定义默认从`config/radar_stock_pools`读取。目标日期不能在未来；历史名录只能使用当日抓取缓存，不能用今天的名录冒充过去。规则版本是本次任务的标识，写入`unconfigured`不会启用尚未确认的数值门槛。

`discover_candidates`采集三个官方名录来源、保存内容寻址JSON产物，再导入共享候选库。名录发现完成只代表资料获取完成，候选仍待核验，正式成员数不会增加。部分来源失败时保存可用资料和来源诊断；全部失败时任务失败，不生成空候选批次。

`refresh_members`只读取有效/暂停成员的入队快照，同股双池只采集一次。可用`--market-window`提供已验证的60个已收盘交易日窗口；缺窗口时行情来源失败，其他来源仍可执行。当前财务、分红和风险后台复核尚未接入，因此成员更新最终保持部分完成或失败，不声称完整复核，也不自动改变成员状态。

## 任务与产物

相同类型、池范围、日期和规则版本只有一个活动任务。整个股票队列同一时刻只执行一项；每次认领使用新租约令牌，90秒过期、15秒续租。过期后最多两次回队，第三次失败；续跑跳过已成功来源，并重新核对产物路径、长度及SHA256。已终结批次保持不变，再次入队创建新批次并复用采集缓存。

候选写入在同一SQLite事务内重验租约；旧执行者不能补写。已完成来源不可改写；失败/部分结果保留历史，最终状态按各来源最新结果计算。资料、审核和成员状态分别保存，采集任务不授予资格。

缓存与产物目录须互不包含，不得包含数据库、池配置、窗口输入或经过符号链接。单份结构化产物64MiB上限，恢复校验按64KiB块读取；原始来源仍遵守各适配器预算，缓存1GiB仅提醒、不自动删除。产物损坏时保留原文件并报告失败，不静默覆盖。

资料发现已接入真实来源；当前尚未提供网页任务入口，CLI运行结果和产物可用于后续页面联调。

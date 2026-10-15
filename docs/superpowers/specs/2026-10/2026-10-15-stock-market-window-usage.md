# 股票研究的统一市场日期窗口

```powershell
stock-robot radar stock-market-window --calendar-db .stock_robot/radar_collector.db --as-of 2026-10-15 --output tmp/market-window.json
stock-robot radar stock-style-evaluate --db research.db --as-of 2026-10-15 --market-window tmp/market-window.json --output tmp/styles.json
stock-robot radar stock-evaluation-capture --db research.db --as-of 2026-10-15 --market-window tmp/market-window.json --output tmp/evaluation.json
```

新命令只读已有缓存，不联网、不刷新或新建日历数据库，不修改股票资料。可用于两池准入、风格研究及评估归档。旧的60日期字符串列表仍兼容；新产物还保存版本、研究截点、有效时点和日历来源。

以北京时间15时作为现有境内市场收盘界线：当天盘中不纳入当天，盘后可纳入；周末和假期按日历已有记录取上个已收盘交易日，历史研究日使用当日日终。拒绝未来研究日、未来缓存抓取时间、坏时区、覆盖不足或少于60个交易日；不会补推工作日，不会因停牌而拿个股更早行情补窗口。

calendar.snapshot_sha256是完整缓存记录的规范JSON摘要，包含days、覆盖区间、抓取时间和来源；不是供应商原始响应摘要。日期窗口仍为research_context_only，不能证明市值、成交额或股票资格已核实，formal_screening_enabled保持false。envelope版本、市场或截点不匹配会拒绝。

缓存缺失或过期覆盖不足时，应先通过已有日历采集流程补齐；本命令不隐式启动更新。输出禁止覆盖日历DB及WAL/SHM/journal。市场价格、市值股类与历史股本仍需独立采集和核验。

SQLite只读边界：命令不更新业务日历、不建表或刷新来源。标准SQLite只读连接在WAL模式下可能创建WAL/SHM辅助文件或维护读锁；不承诺完全零文件变化。保留正常WAL读取，不用immutable忽略尚未checkpoint的已提交更新。新增活动WAL回归验证能读取新source/新交易日/新快照摘要，且读取前后业务行和主库页不变。

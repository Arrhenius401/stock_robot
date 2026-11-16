# 第一阶段：统一股票市场日期窗口

用户授权持续实现无需逐项确认，延续已批准数据/规则阶段；不新增投资门槛、不启用正式筛选或后台自动更新。已读design-before-implementation，按该明确授权执行。

## 已知事实与取舍

准入/风格/评估归档CLI当前允许输入研究日期列表，缺少从现有交易日历生成标准窗口的通用入口。现有radar.calendar.TradingCalendar有只读refresh_enabled=False、cached_data、境内15时收盘与覆盖边界。日历失败不能推断周一至周五，股票停牌不能补更早行情。现有data.akshare.get_total_shares会降级流通股本/财报反推，不能作为全股类市值审核依据。

## 行为与接口

新增radar.stocks.market_context.prepare_market_window(calendar,as_of,*,now)读取一次cached_data，不联网、不建表/更新缓存。只支持XSHG（现有共同境内上市日期口径），未来时间或无时区now拒绝。研究日不能晚于北京now日期；effective_at取研究日北京日终与now较早者，交易日当日15时前排除当日，周末/假期用缓存明确列出的上一个收盘日。覆盖必须包括effective_at的日期，且此前至少60个明确交易日；不足不补工作日、不挪动选择截止日。

输出version=stock-market-window-v1、market=XSHG、as_of、effective_at、days（递增唯一60个日期）、latest_completed、calendar{source,fetched_at,coverage_start,coverage_end,snapshot_sha256}、research_only=true、formal_screening_enabled=false。snapshot_sha256是完整日历记录规范JSON摘要，不冒充原始供应商响应摘要。拒绝无效/未来fetched_at、空source、异常日历覆盖；无额外联网预算。

新增radar stock-market-window --calendar-db 已存在路径 --as-of 日期 --output JSON（必填），只读实例，输出不能覆盖DB及WAL/SHM/journal。新命令注册原CLI。不提供refresh以免读取隐式联网。现有stock-admission/style/evaluation的_read_market_window接受新envelope及旧日期列表；envelope必须版本/market/as_of匹配，其他审核与research_context_only边界不变。输入JSON仍是研究上下文，不是可伪造的已核实准入事实。

## 实施与验证

A类新模块由实现代理完成market_context.py、stock_market_context.py及新测试，父完成解析器兼容、注册、映射/文档；独立合并规格质量审查。测试先红：真实SQLite日历只读/no网络/no副作用、长假/盘中盘后/UTC/历史、覆盖不足、<60、未来/坏时区、输出保护、CLI互通和旧列表兼容。复用已有真实本地日历作只读验收；无有效覆盖则如实记录不伪造。新增模块及测试映射verify限定范围，验证后完整事项本地一笔提交，不推送。此项不是第一阶段全部完成，也不是新交付阶段。

## 实际只读验收与兼容

现有.stock_robot/radar_collector.db含8797个日历记录，覆盖1990-12-19至2026-12-31，fetched_at=2026-10-14T13:59:43.919836+08:00，source=akshare:sina:XSHG。只读命令2026-10-15T15:10生成60日2026-07-16至2026-10-15，snapshot_sha256=4a87b6877fe87165c2a1bfbdda1874d1b760524f69ac831fe1c0d3a63fefcc8b；没有为此联网或刷新日历。

stock-style-evaluate成功读取envelope，仍为研究上下文；stock-evaluation-capture用同窗口为600900生成新不可变档案68fbe32e316997511768c700f200e2bef9a70c6d687c70c2c91303099851aeed，status=complete仅计算完成，freshness.current=true，formal_screening_enabled=false、members_published=0。旧档案不覆盖。验收JSON仅留tmp。

新增envelope兼容先红1失败（旧解析仅接受列表），实施后相关51测试通过；其中新模块28项、原CLI及envelope23项。实现代码Ruff/Pyright通过。最终门禁及审查结果后补。

SQLite只读边界：命令不更新业务日历、不建表或刷新来源。标准SQLite只读连接在WAL模式下可能创建WAL/SHM辅助文件或维护读锁；不承诺完全零文件变化。保留正常WAL读取，不用immutable忽略尚未checkpoint的已提交更新。新增活动WAL回归验证能读取新source/新交易日/新快照摘要，且读取前后业务行和主库页不变。

最终验证：verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv，Ruff通过、Pyright 0错误、375项相关测试通过（51.94秒）；后补活动WAL测试单独1通过并复核Ruff/Pyright。未因仅新增测试与说明重复门禁，未运行全量。A级独立合并规格质量审查最终通过。直接单文件Pyright虽0错误仍提示工作树无本地.venv，正式门禁已绑定共享虚拟环境；不把环境提示藏掉。

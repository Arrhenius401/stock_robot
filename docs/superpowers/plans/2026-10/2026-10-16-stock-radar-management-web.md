# 股票候选、审核与手动任务页面接通

沿用用户已确认的池选择组件和分层管理设计，继续已授权实施，不重新设计侧栏、主题、页面坐标或数值门槛。使用现有CSS变量、panel、subs-btn、radar-note与紧凑列表。当前正式池仍为空，首次真实发布由用户选择且需规则启用；现阶段不能授予资格。

## 用户流程

股票池空态保留说明，准备候选按钮可用；点击在后台执行两股票池共享的指数成分范围发现，显示简短阶段和可展开逐源结果。2026-10-16用户确认红利价值池为A500/中证红利/红利低波并集，成长趋势池为A500/创业板指/科创50并集，详细口径见同目录index-scope计划。全市场名录只作基础索引，不能直接作为筛选候选。切换池或页面不取消任务，返回读取最新状态。管理入口按当前池读取初筛范围并标注未完成资格核验，每页15家，按名称或代码搜索；行业在未核验时显示待核验，不把交易所行业大类当申万两级。

点击公司进入详情，显示资料状态、共同条件与池条件分开核验的结论；来源、原文字段、审核历史按需折叠。已有可确认的原文事实可以保存confirmed/rejected/needs_evidence审核，必须有理由且带公司expected_revision，409提示重新读取。研究线索不能confirmed。未保存意见保留在页面会话草稿，关闭页面有未保存保护；返回保留搜索、页码和详情位置。准备任务不自动审核。

详情可保存当前单公司的不可变评估并保存关注草案；草案仍blocked，明确展示阻碍。正式新增/恢复按钮保持禁用并说明规则尚未配置；API写入也调用原成员门槛，不能只靠前端禁用。暂停/退出历史与草案分开查询。

## 后端契约

独立股票管理router前缀`/api/v1/radar/stocks`，在旧`/stocks/{pool_id}`通配路由之前挂载。股票state_dir默认`Path.cwd()/.stock_robot`，研究及任务共用radar.db内stocks表，缓存和产物放stock-updates/cache与artifacts；与ETF表兼容。只读GET不创建目录/数据库/任务/采集服务：数据库或stocks表未存在时返回真实空集合。读取使用只读连接；写入后仓储可按现契约初始化。

接口：
- GET `/candidates?pool_id=&search=&page=1` → items(symbol,name,revision,data_as_of,admission_status,source_indexes)、total/page/page_size15、各源snapshot_date与完整性、formal_screening_enabled=false。必须从当前池指数范围筛选，不能把旧5225家公司底表当作候选范围；范围缺失时显示未准备或不完整，不退回全市场。
- GET `/companies/{symbol}` → company、evidence、reviews、evaluation（当前只读单公司报告，不存评估；缺市场窗口显示待核验）、publication_enabled=false。
- POST `/companies/{symbol}/reviews`，body evidence_id/expected_revision/decision/reviewer/reason →新review与revision；409冲突，422字段/不能确认。
- POST `/companies/{symbol}/draft`，body expected_revision/request_id/actor/reason/pool_id → 从当前资料现场计算单股评估并归档后创建blocked关注草案；不能接受外部passed/evaluation。
- GET `/drafts?page=1`，GET `/memberships/events?pool_id=&page=1` → 分页历史。
- POST `/membership-changes` → 原成员apply，所有现有门槛仍生效。
- GET `/{pool_id}/members` → pool_id/revision/items/industries/status/unavailable_reason/candidate_count；effective与suspended分开，退出默认隐藏，未知行业不猜。
- POST `/discover`，body pool_id、as_of可选 → 202任务；任务范围统一所有启用股票池，共享发现，规则版本由服务端当前版本组合提供，非客户端决定。
- POST `/{pool_id}/update` →202 refresh_members（空成员422，不调用ETF）；GET `/updates/latest?pool_id=` →任务或null；GET `/updates/{run_id}` →任务；GET `/updates`→分页任务。

写接口均extra=forbid与严格revision；只允许本地标识池和证券；404不存在，409版本/租约冲突，422非法输入，503数据库/服务故障，不能静默失败。产物内部绝对路径不用作浏览器下载URL；前端展示来源HTTPS链接。

## 后台服务

新增仅处理手动queued任务的StockUpdateService，复用StockUpdateRunner。POST第一次使用才初始化并唤醒线程；应用lifespan在已有股票任务表含queued/running时恢复线程，空项目不因只读GET启动。退出时停止领取下一任务，当前任务继续心跳和租约直至完成；进程退出后的未完成任务按租约恢复，不提前释放给其他执行者。不会注册自动计划。服务循环使用Event.wait，runner保留15秒heartbeat/90秒token。成员行情窗口从已有radar_collector.db TradingCalendar缓存只读生成，禁止自动刷新或补工作日。

## 前端契约与验收

继续`renderRadarStockPool(root,header,pool,payload)`，可增加可选options(request,isCurrent)。stock视图自己管理每池搜索/页码/详情和未保存意见，用textContent渲染来源字段，拒绝脚本链接。异步请求及轮询只更新当前渲染，旧响应不能覆盖其他池；任务轮询每2秒、单链不重叠，离开后停止，返回查询恢复。主切换栏仍只有选择和更新；候选准备/管理在内容区。

pool_catalog把股票manage能力改true；update是否可点取决当前有成员和已实现任务，不因正式规则未配置而调用ETF。CSS只新增stock作用域，沿用共享坐标；更新相关CSS/JS入口缓存版本。语义DOM测试覆盖准备、搜索分页、错误与重试、409、保存/保留草稿、旧响应隔离，不能用静态字符串替代真实验收。

S级执行：后端服务/router与API测试代理，前端stock-view/CSS与行为测试代理，父路由接入/能力/缓存版本/兼容/真实验收；独立规格与质量双审及复审。真实服务从工作树显式venv及PYTHONPATH启动8765，完成一次真实会话收发、新功能准备任务及候选搜索/详情/审核拒绝/版本冲突、两ETF池兼容；宽屏与390px检查无溢出及截图，影响全局布局时按AGENTS逐页测量。已批准审核和正式成员选择不能由代理代填；浏览器真实写入只做准备资料与明确测试拒绝，合法审核用隔离合成API数据验证。

## 收尾验收（2026-10-17）

- 已接通独立管理接口、只读研究连接、后台手动队列与当前池指数范围。GET不初始化业务库；写入保留审核CAS和成员门槛。关注草案、正式成员与初筛范围分开展示。
- 独立规格和质量审查均发现同一P2：相同request_id重试重新生成含当前时间的评估，导致误报冲突。改为先按请求标识校对symbol/pool/actor/reason/source_revision并回放；并发提交后也可回放。隔离回归验证跨时刻只生成一份草案和评估，不同输入409。按小修复规则主会话复核差异和用例，未再次派发代理。
- 前端核验项改为中文和默认折叠明细；未保存意见保留，409明确要求重新读取，研究线索不可确认。隐藏股票view后停止界面轮询；后台任务不因切页取消。ETF遗留浮动提示在股票页隐藏，避免390px遮挡切池。
- 相关检查：父接线/目录/只读仓储70项通过；接口、服务和初版DOM19项通过；最终股票与原有雷达/静态页面回归96通过、1个旧缓存版本断言失败，同步断言后该用例单独通过（不声称一次97项全绿）。Ruff通过，定向Pyright0错误0警告。最终中文标签和样式收敛仅补受影响DOM检查，未重复全量。
- 真实工作树启动路径为`D:/code/stock_robot/.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765`，PYTHONPATH明确工作树src及根目录。10月16日浏览器真实点击准备后任务完成，离开报告库再返回能恢复终态；红利范围578家、成长548家，每页15。搜索长江电力、分页2、清除恢复页码、返回详情保留意见均验证，未实际保存代理意见或发布成员。
- 同一真实服务发送“你好，请只回复‘已收到’”，先显示加载再显示“已收到”，发送按钮恢复。10月17日复用该会话与已完成指数产物，不重复联网采集或调用模型。
- 国内及港股ETF仍显示15项榜单，海外ETF显示7项，分别保持10月13日/9月30日行情截点，不冒充当前行情。未修改图表计算或策略语义，原图表验收结果复用。
- 布局实测覆盖个股/指数分析、雷达、报告库、订阅、日志、配置：1280px标题距view顶部24px，内容左侧24px，标题区到首个主要内容16px；390px标题距顶部16px、内容左侧12px，均只有一个可见h1且无页面水平溢出。外层带padding容器与内部主要组件分别核对。窄屏和宽屏股票截图及布局数据保存在主仓忽略目录`tmp/stock-web-proof/`，不提交。
- 边界：正式筛选/成员发布仍关闭，资料准备不等于通过盈利、龙头或风险条件；自动更新仍关闭。完整财务/分红/治理核验未因此完成。没有执行全量门禁。
- 最终补验：中文折叠、隐藏页面停止轮询与草案重试共7项通过；应用集成`tests/api/test_app.py`81项通过。提交范围计划无未映射文件；它按通用规则列出的整个tests/api与tests/radar未全跑，仅复用上述已验证范围，不把未覆盖测试表述为通过。失败产物保留，成功测试临时目录按本次独立路径清理。

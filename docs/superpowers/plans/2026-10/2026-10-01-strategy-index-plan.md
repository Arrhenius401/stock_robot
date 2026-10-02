# 策略指数两期实施计划

日期：2026-10-01。状态：两期已完成并本地提交，未推送或发布。

设计基线：[策略指数分析设计](../../specs/2026-10/2026-10-01-strategy-index-design.md)。

## 实施范围与顺序

第一期完成目录、名称与 ETF 识别及风险收益分析；第二期完成红利和现金流专项数据、覆盖率及报告展示。接口探针事实与口径约束以设计文档为准，以下保留可执行任务及当时的验证记录。

## 模块任务与接口
### 任务1：主会话目录/API/CLI
拥有 data/index_mapping.csv、data/etf_index_mapping.csv、src/data/index_mapping.py、src/utils/symbols.py、src/api/app.py、src/stock_robot/cli.py 及相关入口测试。
IndexStyle增加strategy。IndexMappingEntry新增 provider(csi/cni/sse)、strategy_kind(dividend/dividend_low_volatility/free_cash_flow)、aliases、source_url、price_symbol、base_index，旧CSV可加载。
IndexMapping.resolve(query)按代码/唯一名称/别名返回entry，search(query)返回entries；ambiguous抛ValueError。
ETFIndexMapping.lookup(code)->ETFMappingEntry(symbol,name,index_symbol,source_url)。
GET /api/v1/indices?q= 返回 {indices:[{symbol,name,index_style,strategy_kind,provider,source_url}],etfs:[{symbol,name,index_symbol}]}；POST /api/v1/index 接受名称或ETF，解析成指数target；报告增加 requested_instrument 记录ETF映射，ETF表现使用独立ETF复权序列，不冒充指数收益。
第一批000015上证红利、000922中证红利、H30269中证红利低波动、930955中证红利低波动100、930740沪深300红利低波动、980092国证自由现金流、932365中证全指自由现金流。

### 任务2：数据与策略模块
拥有 src/index/strategy_data.py、src/index/analysis/strategy.py、src/index/analysis/performance.py、src/data/schemas.py、src/index/collector.py、src/index/pipeline.py、src/index/build_single.py、src/report/templates/index_report.jinja2、src/data/akshare.py 及对应测试。
新增 AnalysisResult.dimension 的 index_strategy/index_performance Literal，AnalysisTarget.index_style增加strategy。
IndexAnalysisContext新增 strategy_data: StrategySnapshot|None、benchmark_prices:list[IndexPriceData]、etf_prices:list[IndexPriceData]、requested_instrument:dict|None。
StrategySnapshot/ConstituentSnapshot 定义在 data.schemas：source/as_of/members/coverage/errors/strategy_kind；成员 symbol/name/weight(百分数)/industry/annual_financials，年度记录report_date/available_date/operating_cash_flow/capital_expenditure/net_profit/market_cap/total_debt/cash/dividend_per_share，缺失为None。依赖官方权重+现有行业分类；不得硬编码冒充实时财务。
StrategyDataProvider.collect(target)->StrategySnapshot：目录metadata来自IndexMapping；真实CSIndex/CNI成分权重；公开年度现金流、资产负债、利润和分红接口需探针，按年和公告日选最新可用3年，现金流自由现金流=经营现金流-资本开支、企业价值=同日市值+债务-现金；至少覆盖样本权重80%才可表述为指数估算，否则显示“已覆盖样本估算”。按权重采集全成分、缓存公开数据1天、单请求超时、有限并发，缺失及错误明确呈现，不能每次重复100请求。
collect仅strategy采集专项数据/基准行情，benchmark基准默认000300；performance为区间收益/年化波动/最大回撤/同日基准超额，相同日期交集、不能把价格口径标成全收益。
strategy分析计算 top10集中度/行业覆盖率与行业权重，红利分红连续性/稳定性(年度每股分红非负、缺年不算)/股息率，现金流收益率/连续性/OCF净利润比；各指标独立权重覆盖，公开口径说明、样本日期、财报年度、不可用原因。
两个section由builder写入 IndexReport.section_strategy/section_performance 可选默认None，visible_sections增加strategy/performance；Markdown下载同时支持，旧报告兼容。
策略价数据新增专用fetch分支，不改宽基旧接口；优先可实测源、超时、独立回退；无真实价格pipeline返回errors而不生成全空成功报告。
测试先行验证代码路由、CNI百分数单位、数据排序去重、异常源降级、权重不重归一化冒充完整、NaN缺失、年度公告日/累计口径、样本不足、同日基准交集、价格/ETF复权口径。

### 任务3：Web
拥有 src/api/static/js/indexview.js、src/api/static/js/api.js、src/api/static/js/report.js（只ETF跳转）、src/api/static/js/report-renderer.js、src/api/static/js/app.js、src/api/static/index.html、src/api/static/css/*（确需时）及前端测试。
在指数表单下面用现有panel/details展示“指数目录”，本地过滤名称/代码/别名，按类别分组，点击填入指数代码，支持名称和ETF代码输入；读取 GET /api/v1/indices。
为两个新section添加中文标签和单位/覆盖率/时间字段；不要裸渲染对象/数组。requested_instrument显示识别到ETF及跟踪指数，提供跳转ETF配置雷达入口（cn_hk_etf/515300需核对真实hash）；如果后端有ETF独立表现section标明复权口径。
个股入口输入已知ETF须引导或转指数入口，不能继续旧validate_symbol拒绝515300；沿用动态导入避免循环。
更新CSS/JS缓存版本，遵循共享content坐标与一个h1；不重绘页面。
前端测试检查目录过滤/选中/错误/重复初始化、数据缺失和旧报告兼容。

## 执行与验收
1. 先写失败测试，再实现，主会话整合。
2. 单文件Ruff/Pyright、相关index/data/api/cli/push tests；所有pytest使用项目.venv python、tmp/pytest/<范围>-PID-GUID>独立basetemp与cache。
3. 真实路径 .venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765；PYTHONPATH指向工作树src，确认实际导入路径。真实浏览器发送515300与现金流名称，验证响应、目录、ETF/指数口径、专项指标/覆盖缺失、宽屏与390px、截图与hover（若有曲线）。页面布局改动逐页检查规定页面，无水平溢出。
4. S级独立spec与quality双审查，修复后复审。只跑受影响检查；同步回主项目前完整范围核验，合并前全量门禁显式执行并报告。
5. 保留探针/失败清单和验收记录；没有真实数据的指标不能称为已验收可用。


## 实施期间核验与调整
- 515300官方基金资料核验为嘉实沪深300红利低波动ETF，跟踪930740。修正雷达笼统名称，独立维护跟踪关系。
- 七只新增指数官方源均取得266条日线，2025-08-27至2026-09-30。中证使用官方perf JSON，国证使用官方行情JSON；东财为独立回退。腾讯批量报价和ETF qfq作为独立公开源，通过真实探针。
- 财务改用东方财富公开批量API、有限并发、请求12秒超时、一天SQLite缓存。至少预取四年，按公告/修订日选择可用年度，统一年度窗口避免混年加权。
- 权重采样日与财务截止日、报价日期分别展示。中证当前公开权重2026-08-31，已保留日期及陈旧提示；原始官方权重合计可能因舍入略超100%，不重归一化冒充完整。
- 参考企业价值口径采用同日总市值+年报总负债-货币资金。报告明确其为参考估算，不能声称复现官方因子；市值口径同时独立展示。
- 腾讯市场总市值字段45为亿元，转换为元；每股年度实施派息以每10股派息除10，历史送转股未调整，在方法说明公开。
- 已完成独立需求与质量双审查，修复目录sector键、重复ETF身份、中文日期标签、最新可用年度、Markdown未采集章节标签及同日指数收益对照。
- 最终验收与检查结果已并入本文实施结果。失败产物保留在tmp，成功产物待全部测试停止后清理；不提交缓存与截图。

## 实施结果与阶段验收

### 已交付范围

- 第一期：新增七只策略指数目录、官方来源及别名；支持 H30269、名称解析与歧义提示；维护独立 ETF 跟踪关系，515300 → 930740、510880 → 000015；Web 目录搜索与点选、个股入口 ETF 转指数、API 和 CLI 入口兼容。
- 第二期：真实官方日线与成分权重、公开批量年度财务及实施分红、独立 ETF 前复权日线；风险收益和红利/现金流专项指标；各指标独立权重覆盖率、日期、年度、缺失原因及口径；Web、保存报告和 Markdown 下载。
- 修正配置雷达池中 515300 名称；沿用既有快照，不修改用户配置或历史雷达产物。
- 附带修复：歧义输入清除加载占位；百分比显示舍入；国证空行业不计入覆盖率；旧报告对象可选字段兼容；报告库正文标题降级，保留唯一页面主标题；策略对比表未采集资金面显示不可用。
- 源码、测试、README、计划及本记录已同步到 D:\code\stock_robot，按后端分析与 Web 展示分两笔本地提交；未推送或发布。

### 数据核验

2026-10-01 真实探针中，七只新增指数均取得 266 条官方日线，区间 2025-08-27 至 2026-09-30；515300 取得独立腾讯前复权行情。同日批量报价与年度数据已用于真实专项指标。

2026-10-02 真实 Web 验收受滚动 400 天采样窗口影响，日线为 265 条，区间 2025-08-28 至 2026-09-30；这是日期滚动后的实际区间，页面及报告均明确起止日期。

已核验现金流采用经营现金流减长期资产购建支出、财报公告与修订截止日、实施分红每十股金额换算、同日总市值亿元转元、统一最新可用年度、独立指标覆盖率与基准共同交易日。

### 检查命令与结果

独立工作树：C:\Users\25618\.codex\worktrees\strategy-index-analysis\stock_robot。

1. 在工作树显式执行 `scripts/verify.ps1 -Scope Full`。全量 Ruff、Pyright 通过；首次全量 pytest：1403 通过、9 失败、4 跳过。七个旧 API 测试因测试报告对象缺少新增可选字段失败；另两处为 CRLF 固定换行断言和旧资源版本断言。异常摘要保存在工作树 `tmp/full-check-failures.txt`，失败产物目录 `tmp/pytest/full-1412-8ec826f804be43fa8c2a54eecc943be0` 保留。
2. 修复后工作树相关模块 pytest：140 通过、3 跳过，修改文件 Ruff/Pyright 通过。
3. 当前项目最终相关检查：

```powershell
.venv/Scripts/python -m pytest tests/api/test_app.py::TestIndexEndpoint tests/api/test_static.py tests/api/test_static_strategy_indices.py tests/api/test_index_catalog.py tests/index tests/utils/test_strategy_index_catalog.py tests/test_cli_index.py -q --basetemp <本次唯一目录>/basetemp -o cache_dir=<本次唯一目录>/cache
```

结果：141 通过、3 跳过。新增对比表修正后，仅复核 `tests/index/test_build_compare.py`、`tests/index/test_strategy_extensions.py`、`tests/api/test_index_catalog.py`、`tests/api/test_app.py::TestIndexEndpoint`，38 通过；相关 Ruff/Pyright 无错误。前端 JS 经 `node --check` 验证；`git diff --check` 通过。

所有首次全量失败均已纳入通过的相关复核。修复后未重复全量 pytest；上述结果不表述为最终版本重新跑过全量。最终小修复通过主会话 diff 与受影响测试审查，独立需求及质量双审查已完成。

提交前按功能核对暂存范围，使用 `scripts/verify.ps1 -Scope Changed -Source Staged -PlanOnly` 确认检查计划没有未归类文件；源码状态与已验证状态一致，复用上述通过结果，并补做 `git diff --cached --check`。截图、缓存、配置凭据、日志和测试产物未纳入提交。

### 真实浏览器验收

通过 `.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765` 启动，使用当前项目源码与本地状态目录。

- 指数入口发送 515300，返回跟踪指数 930740；指数价格收益与 ETF 前复权收益分开显示。
- 个股入口发送 515300，自动转入指数入口并返回报告。
- 目录搜索“自由现金流”显示两只候选，点击填入代码；按“国证自由现金流”名称请求真实返回专项报告。
- 单独输入“自由现金流”返回明确 422 候选提示，无残留加载占位。
- “中证全指自由现金流 H30269”真实返回两份报告与对比表；最终 515300/H30269 对比的未采集资金面显示“—”。
- 报告库打开真实 930740 Markdown，ETF 身份、专项分析、覆盖率、日期和同日基准对照均保留。下载 API 返回 200 和 attachment 文件头，正文包含上述章节；内置浏览器未提供下载完成事件，因此未声称已观察到浏览器落盘文件。
- 宽屏 1280×900、手机 390×844 覆盖个股、指数、配置雷达、报告库、订阅推送、日志、配置；标题与首主要组件左边缘对齐，标题区到首组件 16px；桌面内容左边距/标题顶部间距均 24px，手机分别 12px/16px。存在内部滚动条的雷达、报告库和配置内容宽度略小，保留必要滚动条。报告库详情修复后可见 h1 数量为 1。页面及内容容器无水平溢出。
- 查看真实宽屏与手机截图，ETF 输入、按钮、身份卡及指标未被遮挡。首次缩窄视口的侧栏动画结束后正常隐藏，未修改侧栏。
- 浏览器最终未捕获 error 级日志；临时视口覆盖已重置。

截图和布局 JSON 保存在项目 `tmp/strategy-acceptance/`，不提交。早期截图与测量包含修复前状态，最终证据为 `515300-final.png`、`report-detail-wide.png`、`report-detail-mobile.png`、`report-list-mobile.png` 及 `layout-final.json`。

### 已知限制与未验证范围

- 中证最新公开成分权重日期为 2026-08-31，报告提示权重较旧；行业分类覆盖率可能明显低于财务指标覆盖率，不能理解为完整行业分布。
- 官方权重因舍入合计可能略大于 100%，保留原始权重，未强制重归一化。
- 股息率为年度已实施每股分红/采样股价，非 TTM，未调整历史送转股；参考企业价值采用总市值加年报总负债减货币资金，不声称复现官方因子。
- 价格指数收益不含分红；ETF 前复权价格收益不是基金净值全收益；当前成分专项分析不是历史成分回测。
- 两期功能验收时，strategy 采集分支未接入 PE/PB 及历史估值分位，估值栏显示不可用。这是当时的功能边界；后续已按[公开官方估值实施计划](2026-10-02-strategy-index-valuation-plan.md)补齐，不应将本条理解为当前版本仍未接入估值。官方行情源和腾讯替代源已真实验证；东财价格回退受当前网络限制，仅验证异常回退测试，未完成真实成功路径验证。
- 未新增策略指数走势图或改变会话/SSE，相关曲线悬浮与会话流验收不适用。

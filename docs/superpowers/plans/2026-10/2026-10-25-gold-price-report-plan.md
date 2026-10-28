# 黄金综合研报实施计划

依据：同主题设计 `specs/2026-10/2026-10-25-gold-price-report-design.md`，用户已确认并于 2026-10-26 要求继续。停止制作预览；当前范围是完整实现、真实服务验收与本地提交，不推送/合并/发布。

## 工作区与已核验事实

- 独立工作树：`C:/Users/25618/.codex/worktrees/gold-research/stock_robot`，分支 `codex/gold-research`，基点 `e9bbcb5`。
- 原仓 `D:/code/stock_robot` 的其他未提交改动保持原样。工作树无 `.codegraph/`，不建立索引；原仓定位已先用 CodeGraph。
- 复用原仓虚拟环境的 Junction `.venv`，运行时用本工作树 `src` 作为 PYTHONPATH，不修改原环境 editable 安装。所有 Python 用 `.venv/Scripts/python`。
- CacheManager.get 会在过期时删除缓存。黄金自行管理缓存包装中的 expires_at，通过不驱逐的原值接口读取，不修改通用缓存语义。
- 可复用 `radar.provider_request.bounded_provider_call(module,function,kwargs,args,timeout)` 的可终止子进程边界；复用工具不接触雷达池、任务或业务数据。
- ReportFormatter.save 当前分类为 stock/index；报告库 ReportType 为 stock/index/backtest，需要增加 gold，保留路径安全校验。
- 原始来源和样本日期见设计。2026-10-26 追加 IMF SDMX 3.0 与 2.1 候选请求仍超时，全球储备未完成接入验收；本版明确缺失，不自动全国家扫描或使用金价估值代替黄金数量。
- SPDR openpyxl 3.1.5 接口已核验，需用正式解析实样验证两读取器结果一致；标准库为主，可选导入为后备。

## 共享接口（先固定，避免交叉修改）

`src/gold/models.py` 为主会话维护的契约：

- GoldBar(trade_date,open,high,low,close)，有效 OHLC 有限正数。
- GoldObservation(observation_date,value)，允许负利率，禁止非有限值。
- GoldSeries(key,name,source,source_url,unit,frequency,observations,bars,fetched_at,published_at,status,error,parser,notes,metadata)，status=ok/stale/unavailable；as_of 返回原始观察日期。
- GoldCollection(generated_at,series:dict[str,GoldSeries],notes)。key 固定 domestic/international/real_yield/dollar/fx/china_reserves/holdings/global_reserves。
- GoldRules：默认值集中模型，额外字段拒绝；版本化配置 `config/gold.yaml`。

数据接口：`GoldCollector(config_dir:Path,rules:GoldRules|None=None,...).collect(*,now:datetime|None=None,refresh:bool=False)->GoldCollection`。允许注入请求执行器/来源以离线验证，真实请求必须有进程硬截止和总预算。缓存文件位于 config_dir，绝不写雷达数据库。

规则接口：`analyze_collection(collection:GoldCollection,rules:GoldRules|None=None)->dict`，输出严格 JSON 兼容事实：

```text
long_term: direction, confidence, confidence_reasons, reason, windows, factors, missing
markets[domestic|international]:
  name,symbol,unit,status,source,source_url,as_of,latest_close,daily_change_pct,performance
  short_term(direction,reason,confidence,confidence_reasons)
  indicators(ma20,ma60,slope_pct,rsi14,atr14,vol20_pct,vol_percentile,max_drawdown_pct,annual_vol_pct)
  price_position(3y/5y: percentile,start,end,count,status)
  risks[{key,message,value,threshold,active}]
  reassessment[{key,description,value,threshold,met}]
  allocation(label,reason,conditions,batch_guidance)
  trading(label,reason,scope,conditions)
fx_decomposition: [{window,start,end,count,international_return_pct,fx_return_pct,
 domestic_return_pct,international_contribution_pp,fx_contribution_pp,residual_pp}]
```

factors 每行含 key/name/role/weight/source/source_url/unit/as_of/published_at/state，以及 main/marginal（change,start,end,signal,percentile 等）；全球与中国不重复加权。不可用字段用 None，不填零。异常/单源失败不能令整个分析崩溃。

报告接口：`render_gold_markdown(payload:dict)->str`；payload 在以上分析事实外有 report_type=gold,symbol=GOLD,name,generated_at,rules_version,reading_guide,sources,notes,commentary,commentary_status。主会话负责 GoldService，调用 collector→analysis→可选一次 AI→报告构建→归档；保存规则快照供审计。AI 不得改写计算结果。

## 任务与文件所有权

### 任务 A：事实采集与容错（实现代理）

拥有 `src/gold/sources.py`、`src/gold/data.py`、对应 `tests/gold/test_sources.py`、`test_data.py`、`tests/resources/gold/` 中原始解析样本。读取共享模型，不修改模型/配置/入口。

1. 先写真实裁切样本解析回归及失败/可选解析器用例，记录源/日期/裁切方式到本计划附录，由主会话收敛。
2. 实现上金所 OHLC、新浪 XAU JSONP、财政部年度 XML、美联储美元/汇率表、外管局月度数量、SPDR 语义表头 XLSX；global_reserves 明确 unavailable 且说明未通过接入，不伪造全球。
3. 所有日期排序、冲突重复/未来/非法值检查，单位正确；SPDR 后备读取复用同一字节且不绕过语义校验。
4. 黄金独立缓存、旧值保留、观测日期不变；来源日历未知时 6h，能证实的休市/发布窗口最多24h且不跨下一更新点，月度24h、90日门槛留分析层。
5. 真实请求走进程边界，单源30s、全局60s、最多3任务；截止必须终止正在进行的请求，无后台线程泄漏。相关测试使用可控小超时。

### 任务 B：确定性方向与两套建议（实现代理）

拥有 `src/gold/analysis.py` 和相关 `tests/gold/test_analysis.py`，不修改数据/模型/报告/入口。

1. 测试先行：核心2参考1、120/60双窗口、缺参考保留方向、核心冲突不由参考反转；中国/全球同组一票。
2. 真实日期/新鲜度门槛、月度90日、滚动分位预热与中位秩、无未来数据。
3. 五档短期方向、0.5%走平边界、RSI/ATR/Wilder指标、高位与波动风险、3/5年价格位置。
4. 长期配置和技术交易各自门槛、低支撑度降级、风险不改方向；强反弹与长期看空并存。
5. 量化重估、触发/解除带、条件分批示例和汇率交互项/未解释差额；覆盖正/负/零/缺失与共同日期，不产生无效精度或 NaN。

### 任务 C：中文报告表达（实现代理）

拥有 `src/gold/report.py`、`tests/gold/test_report.py`；不改分析/模型/数据/入口。对上述共享结构提供健壮读取，语义字段需对齐后再整合。

1. 先断言阅读指南≤100字，长期/五档方向直接出现，国内/国际与两套建议不混淆。
2. Markdown 包含规则版本、期限、来源/实际日期、单位、证据支撑度及理由、核心/参考与两窗口事实、风险和量化重估、汇率拆分。
3. 数据缺失诚实说明；SPDR仅样本，不把分位写成赔率，不以价格源CFD冒充官方基准；AI禁用/失败不吞报告。
4. 文字/表格转义阻止上游文本注入，不隐藏或杜撰金融事实。

### 任务 D：入口、会话与归档（主会话）

1. GoldService 统一编排/配置/一次解释/报告，测试用确定性数据注入；Config/路径/打包沿用项目。
2. 新增 gold API 路由及金价别名识别、CLI gold、AnalyzeGoldTool 与 bootstrap 注入；AgentCore 新字段默认值保持原调用兼容。
3. 聊天黄金请求提供确定的工具路径，返回结构化 gold 报告并持久化会话产物。复用既有 SSE 类型与历史显示，不将黄金误识别个股/指数。
4. ReportFormatter 和报告库增加 gold 路径/筛选/读取/下载，安全校验不放宽旧分类；报告事实快照与 Markdown 同批次绑定。
5. 现有分析输入与报告/抽屉优先使用 gold 的 Markdown 展示，保持卡片主题/布局，不制作新预览。黄金分类及必要标题文案接入现有控件，更新缓存版本。
6. 加入 verify.ps1 的 gold 源码/资源/配置映射、相关 API/agent/CLI/归档回归，必要 README 中文入口说明；不修改依赖锁或升级版本。

## 验证与审查

- 每个任务运行文件 Ruff、Pyright 与相关 pytest，使用 `.venv/Scripts/python -m pytest`，本次唯一目录 `tmp/pytest/<范围>-<PID>-<GUID>/basetemp` 和同目录 cache。成功后才清理本次目录，失败保留。
- 主会话整合后运行 Changed 计划，补接口/会话/报告库/CLI范围；不默认 Full，不重复已通过且内容未变的范围。
- S级独立规格审查与质量审查，修复后复审。审查代理只返回摘要，不创建正式文档。
- 真实启动 `.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765`，用工作树PYTHONPATH与隔离config目录，不接触现有雷达数据库。真实输入“黄金”、会话发出并响应、切换返回、报告库查看和Markdown下载；宽屏与390px、失败/重试与旧缓存状态验证。不得以静态假页面代替。
- 原始响应/截图/命令日志存 tmp；本计划附录保留来源、验证摘要和未覆盖风险。稳定后源码/测试/设计/计划一笔本地提交，`git diff --cached --check`，不提交预览/缓存/响应/凭据。

## 进度与验证附录

- 2026-10-26：设计批准，工作树和共享接口已准备。IMF候选追加探针超时，全球观察数据仍未验收；后续可选参考，不阻断核心实现。

### 实现结果与规则收敛

- 已实现独立 `gold` 数据、分析、报告与服务；统一网页输入、REST/SSE聊天、Agent工具、CLI、报告库和Markdown下载，不改雷达业务数据。配置沿用现有模型开关，无新增强制依赖。
- 来源字段保留观察日、采集时间、单位、读取器与错误；精确发布日期按时点校验，仅日期精度按来源当地日期校验。日线按设计中的保守闭合规则过滤；历史价格额外保留30日缓冲，确保五年边界覆盖。
- 缓存含来源结构修订、规则版本；已有证据仅覆盖上金所常规周末、财政部周末及H.10常规发布窗，未知法定节假日/国际提供商日历仍6小时。不同黄金报告共享进程内三个请求槽位，等待槽位计入总预算；请求由可终止子进程执行。
- 过热前态使用完整规则快照摘要隔离，恢复→分析→保存处于同一文件锁事务；较旧评估或较旧价格观察期不覆盖新状态，不读取未来评估。状态不可用按未知前态保守检查，写入失败披露。
- 独立交叉审查发现并修复：外管局中间缺月数值错位、精确发布日期前视、年度切换空财政部feed、国际跨午夜盘中条目、annual_days=1非法波动计算、警报未持久化及并发旧前态竞争。补充红→绿回归并复审通过。
- 文档前置长期/国内短期/国际短期方向及两套指引；重估字段采用中文，不以内部键名或“支持因素较多”代替结论。

### 真实样本与采集验证

2026-10-26获取，完整响应留在 `tmp/gold/sources/`，仅下表裁切静态样本进入 `tests/resources/gold/`。裁切保留原字段语义，SPDR仅保留原日期与吨数两列。

| 测试资源 | 来源与裁切范围 |
| --- | --- |
| sge-au9999-history.json | 上金所Dailyhq，末5条，10月19—23日；保留open/close/low/high原列序 |
| sina-xau-history.js | 新浪XAU JSONP，末5条，10月20—26日；含潜在盘中条目供过滤测试 |
| treasury-us10y-real.xml | 财政部年度实际收益率XML，2026年末5个entry，10月19—23日 |
| fed-broad-dollar.html / fed-cny-usd.html | H.10原表末5行及发布日期，含ND，10月13—16日各4个有效值 |
| safe-china-reserves.html | 外管局2026/0206/27116.html，月份表头及1—9月数量行，美元/SDR重复列必须一致 |
| spdr-gld-holdings.xlsx | SPDR官网发现的historical-archive下载，10月15—23日末7行；标准库与可选openpyxl结果一致 |

真实采集首轮13.14秒取得五组宏观/需求证据，定位金价接口所需区间外旧OHLC异常后，先裁切必要年份再严格校验；不修补异常价格。随后统一服务强刷实测6.33秒，七组来源可用，国内/国际3年及5年价格分位有效。全球IMF数量序列仍未验收，按明确缺项降级，不冒充全球央行需求。

### 检查与复审记录

- `verify.ps1 -Scope Changed -Source Staged -PlanOnly`：所有改动已映射，无Full回退、无未映射文件。
- 首次Changed范围检查：Ruff通过、Pyright 0错误；pytest 897通过、5失败。完整首个异常与失败清单保留 `tmp/gold-staged-verify.log`。五项均为测试仍引用旧资源版本导致地址/单例不一致，更新后相关静态与服务范围82通过。
- 最终受影响范围：`tests/gold tests/api/test_app.py tests/api/test_bootstrap.py tests/test_verify_script.py`，262通过，4个现有依赖弃用警告；其余未改状态复用上述Changed已通过结果。最终文案收敛后分析/报告/渲染56通过。
- 当前改动Python范围Ruff通过，Pyright 27文件0错误。源解析/缓存复审29通过，精确发布时间与规则边界复审8通过，年度/日线/警报状态复审3通过，警报事务复审2通过；不将这些重叠用例相加冒充独立覆盖数。
- 通过真实服务入口启动后，浏览器完成黄金输入与报告、首条消息断连/可重试、恢复后发送成功、切换会话并返回原报告、打开报告抽屉与刷新。宽屏1280px和窄屏390px均无页面水平溢出，窄屏标题和主内容左边距12px；表格在容器内滚动。
- 真实HTTP报告库按黄金类型列出5份报告，详情含有效结构化快照；Markdown下载字节与详情正文一致，SHA-256为 `944979fb43aa34d0cf5cb616dd3a67de78374951317be19e1dea657c9ee76a0f`。下载接口完成校验，未通过浏览器下载控件重复验证。
- 成功/失败运行目录均为独立 `tmp/pytest/<范围>-PID-GUID`，缓存同目录。清理组合命令曾被自动审批以 `blocked by policy` 拒绝，产物保留且不提交，不修改ACL或重试删除；此事不改变测试结果。

### 限制与发布边界

- 全球央行数量序列不可用；中国储备仅地区样本、SPDR仅北美ETF样本，长期证据支撑度据此降级。
- 国际CFD参考日线保守滞后，不是实时国际基准价；来源日历未覆盖的节假日不延长缓存。
- 真实验收使用工作树默认隔离状态目录，无模型密钥；验证确定性完整报告及模型禁用/缺失路径。付费模型真实解释未调用，失败/一次调用通过SDK边界注入测试覆盖。
- 研究阈值与分位是版本化假设，未进行预测准确率、投资收益或策略胜率验证。未执行全仓库Full门禁；推送、合并和发布不属于此次授权。

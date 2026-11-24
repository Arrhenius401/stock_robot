# 配置雷达股票池实施与验收记录（含通用分红研究）

> 2026-11-24 文档归属整理：本文件按原始需求收敛阶段、命令说明及执行中修复。下文按来源保留历史状态；“待确认”“未实施”与验收数字只适用于原记录的日期和代码版本，后续确认与路线调整以对应较晚章节为准。本次整理不新增业务授权，也不重新声明当前验收通过。

> 阅读顺序：先查看文末 2026-10-18 结构化数据路线及其后续确认，再按需回看早期 PDF、阶段与研究记录；较晚路线约定不能用于改写早期验收事实。
日期：2026-10-14；压缩整理：2026-10-19。依据：[股票池设计](../../specs/2026-10/2026-10-13-stock-radar-design.md)。状态：阶段一（池目录、正式选择器、空股票池）已实现并验收；完整股票筛选、候选管理与自动更新未实施；通用分红支付率研究工具已实现，正式门槛未启用。

本文合并自 2026-10-13 至 2026-10-14 的实施计划、研究记录、数值实验、分红工具验收及候选明细摘要；原始候选清单与逐项报告保留在 git 历史。

> 历史记录范围：本文保留 dev 在 2026-10-19 的整理状态，不代表后续股票池能力仍未实现。结构化财务及后续研究接线见 [当前实施记录](2026-10-14-stock-radar-implementation.md#source-2026-10-18-stock-structured-financial-implementation)。

## 1. 授权与边界

用户已确认完整分层预览、两股票池名称与流程、持续盈利、行业龙头、黑五类排除、共享候选库、审核发布、手动更新和自动默认关闭；授权在独立工作树推进，明确“先逐项讨论门槛，暂不启用正式筛选”。所有阶段均不授权发布股票池成员，不把研究名单或常量直接变成生产成员。

环境：复用主仓 `.venv`，工作树 `PYTHONPATH` 指向工作树 `src`；验证脚本显式传 `-VenvPath`；新增资源放 `tests/resources/radar/`，完整响应、PDF 和截图留在忽略目录。

## 2. 阶段一：已交付

分支 `codex/stock-radar-pools`，基于真实雷达页面接入，不改动 ETF 原有配置、接口、快照、评分和回测语义。

### 2.1 交付内容

- 只读池目录与股票定义：`src/radar/pool_catalog.py`、`src/radar/stocks/models.py`、`src/radar/stocks/pools.py`、`config/radar_stock_pools/`。
- API：新增 `GET /api/v1/radar/pools`；原 `/universes` 响应不变；股票池使用独立仓储，空集合不伪造快照。
- 前端：`src/api/static/js/radar-pool-picker.js` 及股票视图；接入 `allocation-view.js`、`api.js` 和静态入口；缓存版本 `20261014-pools-2`；验证脚本补映射。
- 股票 ID：`dividend_value`、`growth_trend`；目录配置驱动，`display_order` 决定顺序，禁用/ID 冲突明确报错。
- 选择器：按类型分组搜索、桌面下方面板、窄屏底部弹层、键盘与焦点返回；切换栏只保留选择与更新；海外动态移入 ETF 区，候选/成员管理移入股票区；股票 0 成员时更新/准备候选禁用并显示“规则/采集未配置”。

### 2.2 修复与测试

修复默认池排序、跨池历史快照错配、异步关闭焦点丢失、原型继承属性与合法池 ID 冲突、延迟初始化雷达空白。相关测试 **107 passed / 23.33 秒**；Ruff、定向 Pyright 0 错误 0 警告、JS 语法与 diff check 通过；未跑全量门禁。

### 2.3 真实服务验收

真实服务 8765、工作树 `PYTHONPATH`、隔离验收目录：ETF 快照国内 6 批、海外 5 批；真实会话“请只回复：已收到。”返回完整正文；国内 15/海外 7 成员；510300 截点 2026-09-16，2019-07-18 节点沪深 300ETF 89.6% 对沪深 300 44.1%；6 笔成交保留；跨池历史地址被明确拒绝；海外更新显示真实进度，切到股票仍禁用无串池。1280×900 与 390×844 七页布局无水平溢出；窄屏股票面板宽 366、左右边距 12。

### 2.4 阶段边界

阶段一是池目录和空态，不是选股完成。完整筛选、真实行业成员、证据编辑、发布/生命周期、股票后台采集、自动股票更新均未实现。

## 3. 阶段二至四：计划未实施

- **阶段二：研究底座**。确认设计中的数值门槛后，新增 SQLite `stocks` 前缀表（成员/证据/发布/风险分存）；研究脚本转为参数化采集 CLI（截点、分阶段缓存、失败清单、续跑、可审计来源）；确定性共同准入引擎（持续盈利、规模、流动性、审计治理、金融分支、行业/龙头证据）；独立风格/机会引擎。缺失待核验、监管造假排除、禁止 LLM 放行。
- **阶段三：审核与发布**。审核 revision、硬条件不可绕过、未保存不发布、过期 409；批量原子发布，允许空池或超过 30 家，不自动补位；资格失效同作用两池并停止展示；统一变更清单；股票独立任务类型/快照、幂等、共享采集/限流/续跑。
- **阶段四：管理页面**。行业概览、全池搜索、排序分页、详情、候选管理、变更清单、证据编辑、发布预览；候选 → 审核 → 用户选择 → 发布，未选保持空池。

## 4. 研究链路与关键结果

### 4.1 全市场初筛

- 上市名录 5225 家，剔除 689009 后 5224 家；上市满 3 年 4965 家；无风险名称 4766 家。
- 市值 ≥200 亿元 881 家（≥300 亿元 577 家）；三年盈利 701 家（≥300 亿元口径 469 家）；最新 TTM 盈利 685 家（460 家）。685 家中 669 家在此前 20 家样本之外。
- 685 家仅有增长线索 234 家；4540 条记录至少一项排除；1 家财报缺失但另有排除项。采集覆盖 9 期/108 页；684 家使用 2026 半年报，600206 使用三季报；9 条公告日不合规未使用。

### 4.2 行业、现金与风格

- 申万官方映射 12925 条（2026-10-12 08:55 更新，解析 511 个代码）；5224 家与 685 家均映射到三级行业，共 30 个一级行业。
- 601 家普通企业中 489 家达到三年累计经营现金流/累计归母利润 ≥0.8，112 家未达，84 家金融企业转专项核验；238 家为优先龙头；现金流三年 36 页；84 个数值、20 组行业一致。
- 风格：679 家标准审计、6 家非标准待核验；持续成长 190 家、改善 168 家、重叠 123 家、并集 235 家、均未达 434 家、季度不足 16 家；643 家有连续三年分红记录，42 家未形成；5 家有龙头证据；叠加现金/审计/治理后形成 455/133/128 研究队列。

### 4.3 行情、估值与机会

- 238 家优先候选全部取得行情；80 日完整样本 237 家；60 日日均成交额 ≥1 亿元 236 家（苏泊尔 0.887 亿元、晨光 0.843 亿元未达）；供应商 200 亿元线 236 家（新华文轩 163.27 亿元、龙磁科技 190.59 亿元未达）。
- 均线 + 不过热 + 流动性条件满足 58 家；与成长/改善交集 11 家（长江电力、药明康德、国恩股份、卫星化学等）。PE 覆盖 219 家、PB 覆盖 238 家；960 条成交额一致。
- 示例：长江电力 60 日日均 31.85 亿元、PE 19.86、估值分位 33.99/84.41；国恩股份 PE 26.11、分位 98.02/99.42。相对收益仅作参考，未作正式机会结论。
- 治理：世纪华通建议排除；三七互娱、铂力特、五粮液待核验。

### 4.4 20 家样本核验

覆盖 12 个申万一级行业、196 个单元，195 个非空，1 个分红 TypeError；补采 24 家；4 家因数值排除（京东方、寒武纪、中国铀业、金瑞矿业），16 家待核验，五粮液治理待核验。200 亿元与 300 亿元口径均为 19/20，未出现 200—300 亿元边界样本；没有正式合格或买点结论。20 家不具代表性，不能据此证明 200/300 亿元孰优。

## 5. 数值实验

| 实验 | 样本与口径 | 关键结果 | 结论/限制 |
|---|---|---|---|
| 现金门槛 | 685 家中 601 家普通企业、84 家金融待核；2023—2025 已存财务，未联网；比值为三年经营现金流合计/三年归母净利合计 | 0.6 通过 526（87.5%）、0.8 通过 489（81.4%）、1.0 通过 433（72.0%）；0.8 下家电 10/10、公用 34/35、军工 4/23；北方华创 0.4022、新易盛 0.7343、大华 0.7941、中国交建 0.6441 未达 | 0.8 按风格写入设计，但仍需核验同业前三线索、临界公司及合并/归母口径；实验不等于已启用正式筛选 |
| 流动性 | 2026-10-12 截点 238 家 60 日样本；扩展 685 家 2023-10—2026-10 日线失败 | 238 家中 ≥3000 万/5000 万均 238、≥1 亿 236、≥2 亿 213；苏泊尔 0.887 亿、晨光 0.843 亿；深交所 2024-08 ≥1 亿 913/2847（32.1%），2024-10 2047/2841（72.1%） | 扩展采集连续 ProxyError，仅 15 家成功，不得填零或给全量通过率；样本偏规模/活跃，非全 A |
| 支付率 | 685 家中 643 家观察到连续三年分红，42 家未确认；缓存无实际总额/派息基数，核验完成支付率的为 0 家 | 民生银行三年支付率 30.0156%/30.0429%/30.1410%，均值 30.0665%、累计 30.0640%；反例（利润 1/100/100、分红 2.1/50/50）年度 210%/50%/50%，均值 103.33%、累计 50.80% | 不能用每股×总股本替代实际派息；回购、多股类、股本变动须公告核验；中证红利参考条件与累计比率不等价 |

## 6. 通用分红支付率研究实施

### 6.1 模块与入口

- 计算：`src/radar/stocks/dividend_payout.py`。
- 批量：`src/stock_robot/dividend_research.py`。
- CLI：`stock-robot radar dividend-research`。
- 脚本：`scripts/research-stock-dividends.py`。

输入/输出契约、三年完整年度、Decimal、年度事件完整性、ALL 与 A/H 去重、冲突待核验、缓存与 `rule_enabled=false` 边界见设计稿第 5 节。

### 6.2 验收

- 初版 17 个文件 **134 passed / 23.14 秒**；修复后 4 个文件 **58 passed / 0.56 秒**；Ruff、Pyright 通过，未跑全量门禁。
- 真实 CLI：3 家公司中 1 家完整、2 家待核验；685 家批量 1 家完整、684 家待核验；第二次复用 685 家缓存。
- 局限：信任输入证据，不访问 URL、不证明 PDF 真实；覆盖声明依赖人工；跨 `event_id` 的业务重叠无法完全识别；参考条件不替代连续分红、现金质量、治理、龙头和金融审核。

## 7. 分红资料归档与公告发现

### 7.1 已交付

- `dividend-prepare`：合并旧候选缓存与可选风格缓存，按 symbol 保留利润/每股分红线索，不填实际金额、股类或完整性；记录来源路径与 SHA256，输出交 `dividend-research`。
- `dividend-archive`（`src/radar/stocks/source_archive.py`）：按明确 URL 清单下载 PDF，记录来源、SHA256、字节数、抓取时间、状态与 checkpoint；允许主机 `www.cypc.com.cn`、`static.cninfo.com.cn`、`www.hkexnews.hk`、`static.sse.com.cn`、`www.sse.com.cn`、`disc.static.szse.cn`，其他需 `allow-host`；HTTPS、逐跳验证、并发 2、超时 15 秒、网络/5xx 重试 2、429 停源、1GiB 软上限；HTML 200 视为失败；同 URL 且 SHA 正确时复用，refresh 重取，失败保留旧文。
- 验收：685 家保留 2580 条事件线索；prepared 研究 1 家完整、684 家待核验；长江 2023—2025 三份公告全部下载，字节 739950/218729/150126；重跑全部 cached；**115 passed / 25.20 秒**，Ruff/Pyright 通过。

### 7.2 证据样本

- 长江电力：2023—2025 支付率 73.64%/71.00%/70.92%，均值 71.85%、累计 71.74%；末期派息日 2024-07-19/2025-07-18/2026-07-17。
- 中国神华：2025 末期总额 223.40 亿元、A 股 188.61 亿元、A 股派息 2026-07-13，H 股待核验。
- 美的集团：2025 A 股总股本 69.626 亿股，扣除回购 1.426 亿股后实际 68.200 亿股 × 3.8 元 = 259.16 亿元，派息 2026-06-29；直接用总股本会高估 5.42 亿元，待核验。
- 交易所 2024 年公告返回 HTML 验证页，改从公司官网取得同公告。

### 7.3 未实施

- 官方公告发现：巨潮 `topSearch/query` 与 `hisAnnouncement/query` 已探针（600900 `orgId=gssh0600900`；`tabName=fulltext`、`column=sse/szse`、`pageSize=30`、`seDate`、`isHLtitle`；“权益分派”2024-01-01—2026-10-14 返回 5 条长江公告，`totalAnnouncement=5`、`hasMore=false`；不能据 `totalpages` 判分页；日期为 UTC 毫秒；PDF 为 finalpage 相对路径）。
- 计划中的 `announcement_discovery.py`（双关键词分页、留原始响应、只留实施候选）与 `dividend_extract.py`（校验路径/SHA/PDF 头，pypdf 提字段建议，输出 `event_proposals+requires_review`，不写评估器）及 CLI `dividend-discover`/`dividend-extract` 尚无实施/验收记录。网络预算按串行 1 秒、超时 15 秒、重试 2、429 停源。

## 8. 数据源与字段事实

- 财务 `RPT_F10_FINANCE_MAINFINADATA`：SECUCODE、报告/公告/更新日、币种、收入、归母/扣非；现金流 `RPT_DMSK_FN_CASHFLOW`：`NETCASH_OPERATE`、`CONSTRUCT_LONG_ASSET`；估值 `RPT_VALUEANALYSIS_DET`；审计 `OPINION_TYPE`/`OSOPINION_TYPE`；分红 `PRETAX_BONUS_RMB` 为每 10 股现金，`TOTAL_SHARES` 不是派息基数。
- AkShare 1.18.81 已确认函数存在：`stock_individual_info_em`、`stock_history_dividend_detail`、`stock_fhps_detail_em`、`stock_zh_a_hist`、`stock_financial_analysis_indicator_em`；在线字段完整性、来源一致性和批量耗时仍需实测。
- 现有代码：`config/radar_universes` 只有两 ETF 池；`RadarScoreProfile` 强制 ETF 四因子；`IndustryClassifier` 读取申万一级/二级及 `mapping_status`；`FinancialData` 缺公告日、审计、应收和完整分红，只取 12 期；`AnnualFinancialSnapshot` 有 `available_date`、资本开支、每股分红。

## 9. 验证汇总

- 阶段一相关测试 **107 passed / 23.33 秒**；Ruff、定向 Pyright、JS 语法和 diff check 通过；未跑全量。
- 分红计算：初版 **134 passed**，修复后 **58 passed**；资料归档 **115 passed**；均未全量。
- 真实服务与浏览器：8765 隔离服务，ETF 国内 15/海外 7 成员；真实会话、跨池历史、海外更新、1280/390 布局均按预期；最终行情采集结果未完成。
- 未验证：完整筛选、真实行业成员、发布生命周期、股票自动更新、公告发现/提取 CLI、全量门禁。

## 10. 未实施、未验证与风险

- 阶段二至四全部未实施；股票池仍为真实空池，不能展示研究名单为正式成员。
- 未完成官方 URL 自动发现、任意 PDF 金额/派息基数/多股类/年度完整性提取，也未完成 685 家公告全量采集。
- 主要风险：旧缓存/供应商总股本口径、少数股东口径、追溯修订、幸存者/前视偏差、PDF 验证页与代理错误、输入证据被过度信任、发布/回写恢复已退出成员。
- 待确认：现金门槛最终口径、长期极低流动性、支付率/股息率/估值分位/成长与趋势阈值、单年支付率 >100% 处置、自动调度频率与证据有效期。

## 11. 提交与追溯

阶段一提交位于 `codex/stock-radar-pools` 独立工作树；分红研究、资料归档与相关测试随对应阶段提交。原始候选明细（全市场、质量、风格、行情估值）已压缩收录于本文第 4—5 节，逐行数据保留在 git 历史。本文与设计稿是当前可复核的正式文档；未推送、未合并、未发布。


## 合并章节导航（2026-11-24）

- [2026-10-14 通用分红支付率研究命令](#source-2026-10-14-stock-dividend-research-usage)
- [2026-10-15 共同准入研究使用说明](#source-2026-10-15-stock-admission-research-usage)
- [2026-10-15 两池已确认风格条件研究使用说明](#source-2026-10-15-stock-approved-styles-usage)
- [2026-10-15 股票股本变动研究资料命令](#source-2026-10-15-stock-capital-history-usage)
- [2026-10-15 评估档案与成员草案使用说明](#source-2026-10-15-stock-evaluation-archive-usage)
- [2026-10-15 年报建议到待审规范财务事实](#source-2026-10-15-stock-financial-facts-usage)
- [2026-10-15 季度与半年报研究资料](#source-2026-10-15-stock-interim-research-usage)
- [2026-10-15 股票原始日线与成交额研究](#source-2026-10-15-stock-market-history-usage)
- [2026-10-15 股票研究的统一市场日期窗口](#source-2026-10-15-stock-market-window-usage)
- [2026-10-15 官方沪深证券候选名录](#source-2026-10-15-stock-official-roster-usage)
- [2026-10-15 股票共享候选与证据审核使用说明](#source-2026-10-15-stock-research-review-usage)
- [2026-10-16 股票资料手动任务](#source-2026-10-16-stock-manual-updates-usage)
- [2026-10-13 股票池设计预览验证记录](#source-2026-10-13-stock-radar-design-preview-validation)
- [2026-10-13 全市场初筛的685家研究候选](#source-2026-10-13-stock-radar-market-candidates)
- [2026-10-13 股票池行业与现金兑现研究报告](#source-2026-10-13-stock-radar-market-quality)
- [2026-10-13 股票池全市场数值初筛报告](#source-2026-10-13-stock-radar-market-screening)
- [2026-10-13 优先候选行情估值明细](#source-2026-10-13-stock-radar-market-signals-candidates)
- [2026-10-13 股票池行情估值与最新治理核验](#source-2026-10-13-stock-radar-market-signals)
- [2026-10-13 股票池行业与现金兑现候选明细](#source-2026-10-13-stock-radar-quality-candidates)
- [2026-10-13 股票池风格与风险候选明细](#source-2026-10-13-stock-radar-style-candidates)
- [2026-10-13 股票池风格、审计与首批龙头证据研究](#source-2026-10-13-stock-radar-style-risk-review)
- [2026-10-13 配置雷达股票池：首轮数据与20家样本核验](#source-2026-10-13-stock-radar-validation)
- [2026-10-14 年报发现及财务字段建议实施计划](#source-2026-10-14-stock-radar-annual-financial-evidence)
- [2026-10-14 现金兑现门槛敏感性实验](#source-2026-10-14-stock-radar-cash-threshold-experiment)
- [2026-10-14 官方公告发现与实施字段建议阶段计划](#source-2026-10-14-stock-radar-dividend-discovery-extraction)
- [2026-10-14 普通企业分红实施证据与支付率补充实验](#source-2026-10-14-stock-radar-dividend-evidence)
- [2026-10-14 通用分红资料准备与原文留档阶段计划](#source-2026-10-14-stock-radar-dividend-materials)
- [2026-10-14 红利支付率口径实验](#source-2026-10-14-stock-radar-dividend-payout-experiment)
- [2026-10-14 分红批量工作流及审核资料包实施计划](#source-2026-10-14-stock-radar-dividend-workflow)
- [2026-10-14 通用分红支付率研究基础阶段实施计划](#source-2026-10-14-stock-radar-generic-dividend-research)
- [2026-10-14 通用分红研究基础阶段验收](#source-2026-10-14-stock-radar-generic-dividend-validation)
- [2026-10-14 股票池流动性门槛敏感性研究](#source-2026-10-14-stock-radar-liquidity-research)
- [2026-10-14 股票池第一阶段实现与验证记录](#source-2026-10-14-stock-radar-stage1-validation)
- [2026-10-15 两池已确认风格条件实施计划](#source-2026-10-15-stock-radar-approved-styles)
- [2026-10-15 第一阶段：官方股本变动原文与双日期研究记录](#source-2026-10-15-stock-radar-capital-history)
- [2026-10-15 已确认共同准入条件计算实施计划](#source-2026-10-15-stock-radar-common-admission)
- [2026-10-15 评估留档与版本化成员草案实施计划](#source-2026-10-15-stock-radar-evaluation-archive)
- [2026-10-15 第一阶段：年报建议到规范财务事实的接通计划](#source-2026-10-15-stock-radar-financial-facts)
- [2026-10-15 第一阶段：季度/半年报、TTM与年度事实接通计划](#source-2026-10-15-stock-radar-interim-ttm)
- [2026-10-15 第一阶段：统一股票市场日期窗口](#source-2026-10-15-stock-radar-market-context)
- [2026-10-15 第一阶段：股票原始日线与成交额研究适配器](#source-2026-10-15-stock-radar-market-history)
- [2026-10-15 第一阶段：官方沪深名录与上市日线索](#source-2026-10-15-stock-radar-official-roster)
- [2026-10-15 股票共享候选与证据审核持久化实施计划](#source-2026-10-15-stock-radar-research-persistence)
- [2026-10-16 指数成分初筛范围实施与验收](#source-2026-10-16-stock-radar-index-scope)
- [2026-10-16 股票候选、审核与手动任务页面接通](#source-2026-10-16-stock-radar-management-web)
- [2026-10-16 股票池正式成员生命周期](#source-2026-10-16-stock-radar-membership-lifecycle)
- [2026-10-16 股票池手动更新与候选发现任务](#source-2026-10-16-stock-radar-update-runs)
- [2026-10-17 指数范围年度财务批处理](#source-2026-10-17-stock-annual-batch)
- [2026-10-17 年报任务批量离线重解析](#source-2026-10-17-stock-annual-offline-reparse)
- [2026-10-17 深市年报文本格式兼容](#source-2026-10-17-stock-szse-annual-format)
- [2026-10-18 股票池结构化财务接入实施计划与执行边界](#source-2026-10-18-stock-structured-financial-implementation)


<a id="source-2026-10-14-stock-dividend-research-usage"></a>

## 2026-10-14 通用分红支付率研究命令

> 来源：`docs/superpowers/specs/2026-10/2026-10-14-stock-dividend-research-usage.md`；整理前版本 `ad626dc`。


本工具接受任意候选公司的标准化离线证据，计算三年年度支付率及累计比率，供阈值实验、补证和未来股票池更新复用。它不自动发布成员，不启用正式支付率或股息率规则，不自动从PDF猜取金额。

### 使用

在实施工作树中通过项目环境运行：

```powershell
$env:PYTHONPATH = "$PWD/src;$PWD"
D:/code/stock_robot/.venv/Scripts/stock-robot.exe radar dividend-research --input tests/resources/radar/stock-dividend-evidence.json --output tmp/dividend-research/results.json --as-of 2026-10-14 --cache-dir tmp/dividend-research/cache
```

也可使用同一实现的脚本入口：

```powershell
D:/code/stock_robot/.venv/Scripts/python.exe scripts/research-stock-dividends.py --input tests/resources/radar/stock-dividend-evidence.json --output tmp/dividend-research/results.json --as-of 2026-10-14
```

截止日必须显式提供，研究年度为此前三个完整年度。例如2026-10-14研究2023、2024、2025年，而非过去36个月。脚本从环境导入模块，开发工作树须设置上述PYTHONPATH；安装好的CLI无需自行复制计算代码。

### 输入格式

顶层 `companies` 是公司对象列表，symbol在一个批次中必须唯一；空列表合法。只有symbol的公司也能运行，但会返回待核验。每家公司可提供：

| 字段 | 内容 |
|---|---|
| symbol / name | 股票代码与名称 |
| required_share_classes | 需要覆盖的普通股股类，例如A或A/H；未知不得默认只查A股 |
| profits | year、amount_yuan、currency=CNY、scope=company_ordinary、source_url、disclosed_on |
| coverage | year、complete=true、source_url、disclosed_on，明确该年度全部分红已补齐 |
| events | event_id、fiscal_year、share_class=A/H/ALL、amount_yuan、currency=CNY、source_url、disclosed_on、paid_on |

金额建议用十进制字符串保留元精度；分红为税前实际实施金额，回购不是派息，不将回购金额加到现金红利。普通股利润与对应普通股分红匹配，银行其他权益工具的利润／分红不得混入。来源字段记录事实，不表示程序已访问URL或自动证明材料真实，需要来源适配器或人工核验。

event_id标识同一分红事项，多个股类的同一事项共用ID；同一财政年度中期、末期等不同事项使用不同ID。年度合计标记amount_scope=fiscal_year_total，不能再同时加入同年中期／末期事件；可选covered_event_ids明确覆盖的事件也不能重复输入。覆盖声明须可靠；程序不能仅凭字符串识别跨不同event_id的所有业务重叠。精确重复去重，身份冲突或同事项ALL与A/H并存转待核验。

### 输出与缓存

- complete：本项三年证据满足计算要求，不等于入池或龙头核验完成。
- pending_verification：证据缺失、冲突、股类不齐、截点或金额问题；参考条件为null，不当作不满足门槛。
- annual_average：三个年度支付率的算术平均；cumulative_ratio：三年分红合计／利润合计。
- meets_reference_condition：仅供实验，表示均值及最近一年支付率都严格在0和1之间；100%不通过该参考条件。这个字段只判断支付率，不替代连续三年分红条件；其中一年零分红可能仍满足均值及最近年条件，后续选股必须组合独立分红持续性审核。
- rule_enabled固定false，不写数据库、股票池或计划任务。

缓存按输入证据、截止日及计算版本保存，每家公司独立。重复执行可续用已有结果，证据或截止日改变重新计算，损坏缓存重算。缓存默认不删除，输出完整JSON通过同目录临时文件原子替换；磁盘写入失败明确报错。输入输出同路径拒绝，防止覆盖原始证据。

### 样本与局限

`tests/resources/radar/stock-dividend-evidence.json`包含长江电力2023—2025年官方实施金额及2025年年报利润，另两家公司故意保留证据不足状态。真实来源、采样日期2026-10-14及提取口径见[普通企业实施证据实验](2026-10-14-stock-radar-implementation.md#source-2026-10-14-stock-radar-dividend-evidence)。该资源只保留规范化必要字段，不提交整份PDF。年度合计与中期事件不得重复；年报追溯调整口径不是历史时点回测。

当前通用化的是证据校验和批量计算。第二阶段已补旧缓存转换及明确URL的原文留档，自动发现全市场公告与财务金额标准化仍在后续阶段；没有实际派息证据的685候选不会被这个命令自动放行。数值试验不替代分红可持续性、资本开支、债务、金融专项及治理审核。

### 资料准备与原文留档

第二阶段提供旧缓存转换和明确URL清单的公告留档：

```powershell
stock-robot radar dividend-prepare --candidates tmp/market-candidates.json --style-cache tmp/style-candidates.json --evidence tests/resources/radar/stock-dividend-evidence.json --output tmp/dividend-materials/prepared.json
stock-robot radar dividend-research --input tmp/dividend-materials/prepared.json --output tmp/dividend-materials/research.json --as-of 2026-10-14
stock-robot radar dividend-archive --input tests/resources/radar/stock-dividend-documents.json --archive-dir tmp/dividend-materials/official-archive --output tmp/dividend-materials/archive-result.json
```

开发工作树运行时仍须使用前文共享.venv路径与PYTHONPATH。标准化evidence可选：不提供时仅保留线索。旧候选缓存和style缓存都是公司对象列表，重复symbol报错；旧利润、每10股分红、供应商总股本等保留research_hints，不猜股类或年度事件完整性。输入路径与SHA256记录在准备产物中。

公告输入格式为 `{"documents":[{"symbol":"600900","source_url":"https://公司官方域名/公告.pdf","fiscal_year":2025,"kind":"annual_dividend_implementation","share_class":"ALL"}]}`。fiscal_year/kind/share_class属于调用者提供的描述，不代表下载器已核实PDF内容。相同URL只下载一次，公司描述不丢失。

默认允许已验证官方来源的精确主机：www.cypc.com.cn、static.cninfo.com.cn、www.hkexnews.hk、static.sse.com.cn、www.sse.com.cn、disc.static.szse.cn；其他公司官网可使用 `--allow-host 官方主机名`。只访问HTTPS，逐跳验证主机，无凭据URL/IP/localhost；HTML验证页拒绝，不绕过验证。输入清单和汇总输出必须放在原文归档目录之外，防止覆盖checkpoint或PDF。

最多两个来源并发，同一hostname串行执行以可靠处理429；429后该hostname本轮停止。连接／读取超时15秒，网络超时、连接或分块传输中断、5xx共用最多2次重试预算；流式下载累计耗时可能超过15秒，该参数不是整份文件的总时长截止。状态逐条写入manifest.json，可中断续跑；完整且SHA/大小/PDF头一致的原文默认复用，`--refresh`明确重新下载，失败保留正确旧原文并标出本次失败。缓存超过1GiB只在结果warnings提醒，不删除。

归档产物保留原始URL、最终重定向URL、SHA256、字节数和带时区抓取时间。downloaded/cached仅表示原文采集成功，financial_verified与rule_enabled均为false；不会修改标准化分红金额、成员库或自动任务。

`stock-dividend-documents.json`是2026-10-14从现有长江电力实施证据整理的三条官方URL清单，资源只保存描述与链接，完整PDF保留本地忽略tmp。接口与资源预算测试使用离线响应，不在业务测试中批量联网。

已交付：旧缓存转换、明确URL原文采集、来源级限流、联网原文续跑。第三阶段已新增巨潮A股公告自动发现及支持模板的字段建议；更广来源、任意旧PDF结构及股类／年度完整性补证仍待后续，支付率数值门槛未启用。

### 公告自动发现与字段建议

第三阶段可从公司代码发现官方公告，再串联原文留档和字段建议：

```powershell
stock-robot radar dividend-discover --input tmp/dividend-materials/prepared.json --output tmp/dividend-auto/discovery.json --as-of 2026-10-14 --cache-dir tmp/dividend-auto/query-cache
stock-robot radar dividend-archive --input tmp/dividend-auto/discovery.json --archive-dir tmp/dividend-auto/pdf-archive --output tmp/dividend-auto/archived.json
stock-robot radar dividend-extract --archive-result tmp/dividend-auto/archived.json --archive-dir tmp/dividend-auto/pdf-archive --output tmp/dividend-auto/extracted.json --as-of 2026-10-14
```

开发工作树仍使用共享.venv和前文PYTHONPATH。发现输入可为公司列表或companies对象，精确A股代码与官网orgId匹配；两关键词为权益分派和利润分配，起点为截止年前第三年1月1日。支持分页、原始JSON缓存和refresh，源请求串行间隔1秒，15秒连接／读取超时、最多2次重试、429停止该来源。合法零结果的公告列表null仅在总数明确为0且无后续页时接受；字段缺失不能当零。

query_scope_complete只指这两个关键词的指定日期查询范围完整，不证明该公司三个财政年度分红资料已齐。标题高亮清洗，公告时间戳按北京时间解读，输出disclosed_on。实施候选及相关取消／终止／更正／调整等变更风险公告留档；后者独立discovery_status=pending_review，不因下载成功覆盖提醒。

提取器先核对URL对应安全文件名、SHA、大小和PDF头，使用同一已核验字节解析。支持明确的年度、中期、半年度实施模板及A股重要提示变体；本次实际金额、全年合计、中期参照分别输出，不相加。美的历史股东会方案金额与实施金额分段，回购后实际基数不明时不能拿总股本替代；A/H日期或金额不明保持待核验。扫描件与不支持的旧结构不能自动解释为无分红。

event_proposals沿用amount_yuan、fiscal_year、source_url、disclosed_on、share_class、paid_on等字段。requires_review恒true，financial_verified与rule_enabled恒false。matched_template表示支持模板提取，不表示已经核实金额、股类、利润或分红持续性；proposals不能直接作为研究命令的companies财务输入。审核确认后才整理标准化证据，程序不会自动填events、profits或coverage。

同时明确每股、实际参与基数与本次总额时，进行Decimal关系检查；允许≤0.01元的保守展示舍入差，明显不一致转待核验。较大单位的舍入差可能保守待核验，不自动放大容差；缺总额不得按股本乘每股金额推算。

本阶段新增原始零结果JSON及十份裁切文本资源。原始异常样本保留尾随空白、标题重字、历史审议误导段和中期变体，来源与裁切范围见[发现与提取计划](2026-10-14-stock-radar-implementation.md#source-2026-10-14-stock-radar-dividend-discovery-extraction)。整份PDF和查询响应留在忽略tmp。

实际小样本三公司查询发现14份实施候选，解析复验10份匹配、4份待核验；这个数量不是公司支付率通过率或入池数量。仍待后续：更广来源与旧结构支持、年度／股类完整性及利润核实、经审核资料的标准化证据汇总。正式选股门槛仍未启用。

### 通用批量工作流与审核资料包

新增一个入口串联已有命令，不用逐公司或逐阶段手工重复运行：

```powershell
stock-robot radar dividend-workflow --input tmp/companies.json --workspace tmp/dividend-workflow --as-of 2026-10-14
```

输入可为公司对象列表或 `{"companies":[{"symbol":"600900","name":"长江电力"}]}`。输入必须放在workspace之外；工作目录绑定公司输入文件SHA和截止日，不同研究输入使用不同目录，不能静默混入旧结果。`--refresh`明确刷新查询与PDF，失败仍会记录本次失败；默认继续使用原采集器的来源级限流、重试及缓存核验。

已经核验并整理的规范证据可通过 `--evidence 规范证据.json` 显式提供，格式仍为现有companies结构；可以在同一工作目录补证后重跑，证据文件SHA记录在本次产物中。提取建议永不自动填入events、profits或coverage，供应商旧缓存仍仅是研究线索。

工作目录保留发现、原文留档、提取、证据准备、支付率研究和review-packet.json。审核资料包逐公司列出此前三个完整财政年度、未解决事项、原文失败原因、带稳定编号的建议及原文SHA／摘录，便于后续审核，不会删除没有成功提取建议的公司。年度证据行计数只表示字段存在，不表示已经核实，查询范围完成也不表示年度分红完整。

review-packet.json的revision_sha256绑定整包内容；建议proposal_id绑定公司、字段内容及原文摘要，排序改变不改变建议身份。它是核验入口资料，不是批准记录，requires_review=true，rule_enabled=false；正式入池和自动更新保持原状态。

### 年报财务证据建议

工作流可显式增加年报发现、原文留档和财务前页解析：

```powershell
stock-robot radar dividend-workflow --input tmp/companies.json --workspace tmp/dividend-workflow --as-of 2026-10-14 --annual-reports
stock-robot radar financial-extract --archive-result tmp/dividend-workflow/annual-archived.json --archive-dir tmp/dividend-workflow/annual-pdf-archive --output tmp/financial-proposals.json --as-of 2026-10-14
```

`--annual-reports`默认关闭，开启后增加独立查询和年报PDF下载，继续使用15秒连接／读取超时、两次重试、429停止来源和1GiB软缓存提醒；源停止会作用本轮后续年报阶段。分红和年报查询缓存分开，错误不会被旧成功摘要覆盖。没有新增运行依赖或自动任务。

新文件为annual-discovery.json、annual-archived.json、financial-extracted.json，审核包增加financial_documents和financial_proposals。报告摘要、英文版和普通审议意见不会认作完整年报；修订、取消、补充、日期变更等保留风险状态。这里只确认年度报告关键词查询范围，不证明所有财务披露均已取得。

PDF最大25MiB、1000页，核验URL对应文件名、实际大小和SHA后，只提取前20页的主要会计数据。支持收入、归母净利润、扣非归母净利润和经营现金流；明确的人民币单位用Decimal换算。标准四列可对应三个连续年度；调整多列仅提取当前报告年度，原始比较值保留待审。负值保留；错误千分组、缺失列、游离符号、金额列百分号、冲突或同页多主表不能猜值。扫描件、不支持结构或超预算报告保留待核验。

建议口径为reported_parent或consolidated，不自动转换为普通股股东利润，不推断优先股／永续工具、股类或年度派息完整性。所有financial_proposals仍需审核；--evidence是唯一规范财务输入入口，财务提取不会替代它。支付率研究及股票池成员状态不会因模板匹配而自动放行。

真实长江样本：四份年报生成24条建议（其中三个目标年度各4条当前年度建议），另有一份日期变更风险资料待复核；这是字段建议数量，不是公司通过率。来源及原始异常样本裁切范围见[年报财务证据计划](2026-10-14-stock-radar-implementation.md#source-2026-10-14-stock-radar-annual-financial-evidence)。


<a id="source-2026-10-15-stock-admission-research-usage"></a>

## 2026-10-15 共同准入研究使用说明

> 来源：`docs/superpowers/specs/2026-10/2026-10-15-stock-admission-research-usage.md`；整理前版本 `ad626dc`。


本阶段承接共享候选及证据审核。入口为 `stock-robot radar stock-admission-evaluate --db PATH --as-of 2026-10-15 --output REPORT.json`。可用 `--symbol 600900` 只计算单家公司；不传则计算全部共享候选。输出独立研究报告，不修改数据库中的公司资格，不发布成员。

### 如何提供事实

先按《共享候选与证据审核使用说明》通过 `stock-evidence-add` 添加明确来源、SHA256及披露日期的规范事实，再以 `stock-evidence-review` 显式审核。导入候选、自带 verified 字段、PDF模板解析建议均不能代替这一步；审核表示事实可信，不表示符合投资标准。

规范事实分为七种 kind（字段完整约定见共同准入实施计划）：

| kind | 核验内容 |
| --- | --- |
| admission_security | SSE/SZSE普通A股、首次A股上市日期、企业类型 |
| admission_capitalization | 人民币公司全股类市值，当前及60个市场交易日完整序列 |
| admission_profitability | 最近三个完整年度归母及扣非归母利润、独立最新TTM |
| admission_audit | 三个完整年度审计意见与整改核验 |
| admission_governance | 治理专项结论及依据 |
| admission_leadership | 一级/二级行业、业务范围及龙头证据 |
| admission_financial_quality | 与企业类型匹配的财务专项，金融单独核验 |

示例规范payload（仅展示字段，不是实际公司的事实）：

```json
{"exchange":"SSE","security_type":"ordinary_a","a_listed_on":"2010-01-01","issuer_type":"ordinary"}
```

资料来源、披露时点、审核时点、90天有效期及归属均须有效。同一组存在相互矛盾的有效事实则待核验，不选择有利的一份。人工给出 clear 也不能绕过市值、上市年限和利润数值；任何确定性硬条件失败保留失败，缺失项另列待核验，不填零。

### 市场窗口与结果

可选 `--market-window PATH.json` 输入60个严格递增、唯一、已经核验的市场交易日ISO日期列表。不能把任意60条个股行情或自然工作日当交易日历。未提供窗口时市值时点与60日覆盖待核验。报告记录输入路径及SHA256，此文件只是研究上下文；后续正式发布还须绑定生产交易日历和行情版本。

报告每家公司保留 `source_revision`、`as_of`、`rule_version`、来源证据ID及逐项 checks。计算期间公司资料revision变化会失效并提示重算；单家公司异常不丢失其他公司的结果。

`common_status` 为 passed/failed/pending_verification。已确认硬条件失败时研究报告的 `admission_status` 为 excluded；即使共同条件全部通过，最终仍为 pending_verification，因为风格及机会规则尚未完整配置。`formal_screening_enabled=false`、`members_published=0` 始终保持。报告中的 excluded 不等于监管认定或数据库成员淘汰操作。

本阶段不启用尚未确认的股息率、估值、成长或趋势数值，不将流动性1亿元或成长池现金流0.8变成共同淘汰条件。不负责自动抓取七组规范事实，也不构成历史时点回测：当前审核状态不是过去审核历史的重建。


<a id="source-2026-10-15-stock-approved-styles-usage"></a>

## 2026-10-15 两池已确认风格条件研究使用说明

> 来源：`docs/superpowers/specs/2026-10/2026-10-15-stock-approved-styles-usage.md`；整理前版本 `ad626dc`。


日期：2026-10-15。共同准入入口保持原样；新入口：

```powershell
stock-robot radar stock-style-evaluate --db PATH --as-of 2026-10-15 --output styles.json
```

可用 `--symbol CODE` 只计算一家，或用 `--market-window window.json` 提供已核验的60个市场交易日研究上下文。数据库和输出保护、审核有效期、修订竞争检查与共同准入入口一致；该命令仍然不发布成员。

### 事实准备

用 `stock-evidence-add` 加入下面的规范事实，再用 `stock-evidence-review` 显式审核。证据必须有来源URL、原文SHA256、披露日期、公司归属；输入内自带verified或clear不能代替审核。来源/审核过期、同组冲突、期间或口径缺失均待核验，不填0。

| kind | 主要内容 |
| --- | --- |
| style_cash_flow | 普通企业三个完整年度合并经营现金流，已核对归母利润比较口径 |
| style_cash_review | 现金及分红支撑专项依据，issuer_type须与证券事实一致；金融单独核验 |
| style_dividend_history | 三个完整财政年度实际已实施全股类现金分红人民币汇总及完整覆盖 |
| style_liquidity | A股原始日成交额，逐日匹配60个市场交易日 |

详细字段见[实施计划](2026-10-14-stock-radar-implementation.md#source-2026-10-15-stock-radar-approved-styles)。现金比率的分母只取同公司已审核 `admission_profitability` 的年度归母利润，不能另填一个有利分母；原始报告解析建议仍须人工核验规范化。

示例现金流payload（合成字段示例，不是实际公司事实）：

```json
{
  "issuer_type": "ordinary",
  "currency": "CNY",
  "scope": "company_consolidated",
  "parent_profit_scope_reconciled": true,
  "annual": [
    {"year": 2023, "operating_cash_flow_yuan": "80", "disclosed_on": "2024-04-01"},
    {"year": 2024, "operating_cash_flow_yuan": "80", "disclosed_on": "2025-04-01"},
    {"year": 2025, "operating_cash_flow_yuan": "80", "disclosed_on": "2026-04-01"}
  ]
}
```

### 如何理解结果

- 普通企业红利价值池：三年经营现金流合计/三年归母利润合计>=0.8为硬条件，另须现金专项核验；金融不套该比率，用类型匹配的专项事实。
- 成长趋势池：同一比率只是质量指标，低于0.8提示关注，不因此淘汰。未确认的应收/存货/偿债数值触发条件不启用；共同资格里的真实财务风险仍独立检查。
- 红利分支：三个完整财政年度均有已实施现金分红，不要求逐年增加。没有实施分红必须有完整覆盖证据，未知不当作零。价值分支不强制连续分红，红利分支失败不会自动淘汰价值分支。
- 60日日均原始成交额低于1亿元：红利价值显示提醒；成长趋势的当前机会继续观察。均不直接取消公司资格，资料不足则机会暂不可判断。

`pools` 包含 dividend_value 与 growth_trend，分别返回检查、已确认条件状态、风格状态、资格状态、机会状态及待配置规则。红利价值还保留 dividend/value 两分支。`approved_conditions_status=passed` 只代表本阶段已确认条件完成，不代表完整风格合格或允许发布。

未确认的支付率、股息率、估值、成长及趋势参数保持待配置；共同通过、现金和流动性单项通过都不等于当前机会成立。`formal_screening_enabled=false`、`members_published=0` 始终保持。版本变化或单家公司异常时嵌套结果全部失效，旧数值仅供排查。该命令没有自动采集、自动成员轮换或生产发布功能，也不重建过去时点的审核历史。


<a id="source-2026-10-15-stock-capital-history-usage"></a>

## 2026-10-15 股票股本变动研究资料命令

> 来源：`docs/superpowers/specs/2026-10/2026-10-15-stock-capital-history-usage.md`；整理前版本 `ad626dc`。


此命令接通巨潮资讯公开股本变动原文，为后续审核提供可追溯资料。**采集完整不等于市值资格通过**。单位、全部股类覆盖以及GDR底层股份映射尚未核实，命令不换算股数、不计算市值、不写审核或成员。

### 使用

在实施工作树运行，显式复用主仓库环境：

```powershell
$env:PYTHONPATH="$PWD/src;$PWD"
& D:/code/stock_robot/.venv/Scripts/stock-robot.exe radar stock-capital-history `
  --input tmp/companies.json --as-of 2026-10-15 --start-date 1990-01-01 `
  --cache-dir tmp/stock-capital-history-cache --output tmp/capital-history.json
```

输入可为公司对象数组，或含`companies`数组的对象；每家公司须有合法沪深普通A股六位`symbol`，不得重复。默认起点1990-01-01只表示查询范围，不是公司上市日期或历史覆盖证明。未来截点、倒序范围、覆盖输入或缓存内部的输出会在请求前拒绝。`--refresh`重新采集，失败不会伪装成旧缓存成功。

### 输出口径

逐公司保留URL、查询范围、SHA256、实际采集时间、原文归档名、原始字段、公告日与变动日。只有两个日期都不晚于研究截点的合法记录才作为该截点研究记录；未来变动、截点后公告、身份冲突、重复等单独诊断，不择优取数。

`F003N`、`F022N`等保留来源字段。`F022N`仅为流通股份中的人民币普通股，不是全部A股；不能用它加H股后推断历史公司全部股份。`unit_verified=false`、`all_share_classes_verified=false`和`financial_verified=false`始终保留，缺失值不填零。对历史截点查询当前接口会标明回溯研究，`point_in_time_verified=false`，不冒充过去当天保存的快照。

`complete`只说明本次请求范围的返回数量与合法解析情况，不证明所有历史事件无遗漏。即使完整也没有`capitalization_fact`，不能直接用作200亿元市值筛选事实。

来源：[巨潮资讯公开API文档](https://webapi.cninfo.com.cn/#/apiDoc)，接口`p_stock2215`。字段映射参考已安装AkShare `stock_share_change_cninfo`；单位仍须补官方说明或独立原文审核，不能依数量级反推后认定已核实。

### 请求、缓存及失败

复用已有AkShare和mini_racer，不新增依赖。只执行安装包内可信时间编码函数以生成公开请求头，不保存动态头；不处理账户或验证码。生产请求含头生成与完整响应读取总截止15秒，单响应4MiB，串行间隔1秒，网络/5xx最多重试2次（沿用功能设计第6节，重试退避3秒、10秒）。401、403、429或权限/验证码挑战会停止该来源后续网络，已有效缓存仍可读取。

HTTP200预算内原文先按内容摘要归档，再严格解析。缓存复用保留首次采集时间并重新解析；异常缓存不得生成成功事实。1GiB缓存软上限仅提醒，不自动删除。完整原文、缓存、错误诊断和真实验收输出只放忽略目录`tmp/`。


<a id="source-2026-10-15-stock-evaluation-archive-usage"></a>

## 2026-10-15 评估档案与成员草案使用说明

> 来源：`docs/superpowers/specs/2026-10/2026-10-15-stock-evaluation-archive-usage.md`；整理前版本 `ad626dc`。


日期2026-10-15。此功能是本地研究留档及关注变更准备，尚未接入正式管理页面和成员发布。使用同一个研究SQLite数据库，新增独立stocks表，不改变ETF记录。

### 计算并留档

```powershell
stock-robot radar stock-evaluation-capture --db PATH --as-of 2026-10-15
stock-robot radar stock-evaluation-list --db PATH
stock-robot radar stock-evaluation-list --db PATH --evaluation-id ID --output evaluation.json
```

capture始终由服务端读取候选/证据、重新计算共同及两池风格，不接受外部passed报告。可加 `--symbol CODE` 只研究一家；市场窗口通过可选 `--market-window PATH.json` 提供，规则同stock-style-evaluate。输入输出禁止覆盖数据库、其日志或市场窗口输入。

每份档案包含完整报告、当时的公司和证据原文资料、最新审核身份及决定、资料修订号、规则版本、研究截点和市场窗口来源。相同内容重复归档复用同一ID和首次保存时间；新证据/新审核不会覆盖旧档案。

`status=complete` 仅表示这一批计算执行完整，不表示股票合格。部分失败保留为独立partial档案，不覆盖旧complete，也不能用于成员草案。`freshness.current=true` 只表示研究来源/日期/规则仍适用，不是入池批准。公司资料、全范围名录、规则、研究日期或审核90天有效期变化，会动态标示失效；历史原文仍保留。该功能不重建过去时点的审核历史，不构成股票回测。

### 保存新增关注草案

准备JSON文件：

```json
{
  "evaluation_id": "替换为实际档案ID",
  "changes": [
    {"pool_id": "dividend_value", "symbol": "600900", "action": "add", "reason": "填写自己的关注理由"}
  ]
}
```

以上仅为字段示例，不是对该公司资格或入选的认定。

```powershell
stock-robot radar stock-member-draft-create --db PATH --input changes.json --request-id my-request-1 --actor 本地用户
stock-robot radar stock-member-draft-list --db PATH
stock-robot radar stock-member-draft-list --db PATH --draft-id ID --output draft.json
stock-robot radar stock-member-draft-cancel --db PATH --draft-id ID --expected-revision 1 --actor 本地用户 --reason 撤销关注
```

股票池目录缺省 `config/radar_stock_pools`，可用 `--pool-dir PATH` 指向实际定义。池必须存在且启用，并有对应风格计算结果；新增一个池名字不会自动获得规则。草案导出不能写入读取中的池配置目录，避免覆盖定义。

创建时要求引用当前、完整的研究档案。每项change只允许pool_id/symbol/action/reason，当前action只支持add；同池同公司不能重复。任意一项无效则整批不保存。相同request-id及相同规范内容重放不会重复创建；同request-id异内容报冲突。资料变化后重放只读回原草案和当前失效标记，不恢复资格。

草案revision从1开始，状态blocked，保存逐项资料版本、理由和阻碍（例如共同条件待核验、风格未配置）。撤销须携带当前revision，追加操作人、时间和理由，历史不会删除。旧revision不能再次撤销。操作人字段是本地审计署名，不是身份认证或入池权限。

当前始终 `publication_enabled=false`、`members_published=0`，没有发布命令，也没有写正式成员的入口。草案保存不会把公司放入股票池。成员发布、暂停、退出、恢复、后台更新及对应管理页面仍是后续任务。


<a id="source-2026-10-15-stock-financial-facts-usage"></a>

## 2026-10-15 年报建议到待审规范财务事实

> 来源：`docs/superpowers/specs/2026-10/2026-10-15-stock-financial-facts-usage.md`；整理前版本 `ad626dc`。


日期2026-10-15，属于四阶段中的第一阶段数据接通。此功能整理已有年度及季度建议，不增加网络采集或投资门槛。

### 准备与保存

```powershell
stock-robot radar stock-financial-facts-prepare --db PATH --symbol 600900 --as-of 2026-10-15 --output prepared.json
```

默认只读。命令从研究库读取原始financial_proposal，整理最近三个完整财政年度的归母利润、扣非归母利润、经营现金流和收入；有季度资料时同时计算TTM建议。每项都保留数据库证据ID、实际PDF URL/SHA256、页码、原文、单位及审核身份；原始金额格式与规范Decimal金额分开保存。

输出facts可包含admission_profitability和style_cash_flow；annual_summary供逐年度查看覆盖情况。等值来源合并，不同数值冲突保持None；不挑最新或有利的一份。不适用年份/无效来源会列未采用诊断，不能把诊断数量当作公司经营风险数量。整理结果仍requires_review=true、financial_verified=false，尚无原始建议时不会伪造事实。

如需保存，用准备结果里的source_revision作版本：

```powershell
stock-robot radar stock-financial-facts-prepare --db PATH --symbol 600900 --as-of 2026-10-15 --save --expected-revision N
```

该命令重新读取并准备，再在同一事务校验版本和来源。它不接受外部passed JSON；版本变化或任何组无效整批失败，不留下半份组。保存只增加待审证据，不增加审核记录或正式成员。相同内容重存复用证据，但仍须当前版本。

### 审核与失效

保存结果saved.evidence_ids提供可查看的证据ID；使用现有stock-records-list查看完整来源，再通过stock-evidence-review显式审核。saved.revision只代表该次保存后的公司版本，每次审核后版本继续递增，应重新读取后再操作下一条。

利润年度规范映射须核对归母范围。无季度资料时TTM仍为null；有季度资料时按上年全年+本年累计-上年同期累计计算，完整公式、三个分量和来源保留于ttm_summary。任何分量缺失或冲突都不猜值。不能用单季度金额替代累计，亦不以旧季度或年报直接补最新TTM。现金组parent_profit_scope_reconciled始终false，发行人类型只从有效审核过的证券事实取得，否则未知。简单确认年报组不会自动完成现金与归母利润口径核验。TTM数学结果不等于最新范围核实；latest_announced只在显式已审核admission_reporting_coverage证明当前期间、覆盖至研究截点、报告类型完整且口径可比，并且归母/扣非分量齐全时为true。该证据的basis必须有内容并满足现有来源和90天审核有效期要求。查询完成不会自动产生此确认。

payload._financial_dependencies为服务器保留字段，含准备截点as_of、该公司全部上游财务建议及其审核版本、实际采用的证券来源。利润组有季度资料时使用version=2，绑定全部年报建议、季度建议、失败季度文档及覆盖审核；现金组仍为version=1、只依赖年度和证券口径。保存、确认和读取时从当前数据库原文重新准备并精确核对整个结果；保留原文引用却修改金额、披露日、TTM或现金口径会被拒绝。既有无该保留字段的手工事实仍按独立来源及审核流程管理。

上游新增建议、审核变化、驳回或自然到期，均可能使旧组lineage_status=stale、review_status=needs_evidence。旧审核历史不删除，但不再供规则放行；需重新准备并复核。不相关的普通提示不会使财务组无条件失效。

命令输出禁止覆盖SQLite数据库及其日志。实际研究数据和导出文件留本地，不自动审核、发送或发布。formal_screening_enabled=false、members_published=0始终保持。本项已接通季度/TTM研究资料，仍未完成全市场行情及行业接通，也未完成第一阶段全部工作。

### 失败报告与重新准备

较新季度发现但下载/提取失败时，仍保留合法公司、来源、披露日和报告期线索；即使没有PDF摘要，也会阻止把旧完整季度称为最新。该线索不提供任何金额或确认能力。发现/PDF期间错链时两个原期间留存，金额被清空。

上游新季度会使旧年度利润规范组失效，避免沿用缺最新TTM的旧组；不相关季度不使年度现金组失效。coverage审核变更、过期或较新报告出现后，应重新准备和复核。保存前后均从数据库原文重算，保留引用但篡改TTM金额、latest_announced、公式或范围会被拒绝。


<a id="source-2026-10-15-stock-interim-research-usage"></a>

## 2026-10-15 季度与半年报研究资料

> 来源：`docs/superpowers/specs/2026-10/2026-10-15-stock-interim-research-usage.md`；整理前版本 `ad626dc`。


本功能属于第一阶段数据接通，发现Q1/H1/Q3完整报告，并从已归档PDF提取累计字段。不会审核事实、确认最新报告或发布股票池成员。

```powershell
stock-robot radar interim-discover --input companies.json --cache-dir tmp/interim-cache --as-of 2026-10-15 --output discovered.json
# 用现有dividend-materials归档发现的documents，沿用官方来源、大小及hash校验。
stock-robot radar interim-extract --archive-result archive.json --archive-dir tmp/pdf --as-of 2026-10-15 --output extracted.json
stock-robot radar interim-review-packet --input extracted.json --as-of 2026-10-15 --output packet.json
stock-robot radar stock-records-import --kind review_packet --input packet.json --db research.db --as-of 2026-10-15
```

公司输入沿用公告发现器companies格式。发现仅查询对应关键词，查询成功不等于最新报告范围已核实。摘要、英文和特殊版本不会自动变成可用事实。请求与缓存预算沿用已有实现。

支持明确人民币单位和已核验布局：Q1/H1当前及上年同期，Q3只取年初累计；调整后比较列保留重述标志，不能据此自动认定可比。归母利润、扣非归母利润、经营现金流、收入分别保存原始列、页码、摘录、单位及PDF摘要。真正多金额表、冲突单位、未知列布局、身份错误或期间错链都保持待核验，不猜金额。

发现与PDF期间冲突时清空金额建议，记录两个原期间；合法较新期末仅作为阻止回退旧报告的线索，不是已核实财报。提取失败的文档也可留档。导入产生quarterly_financial_proposal与quarterly_financial_document，全部未经审核，已有年报字段不受替换。第一笔功能尚未计算TTM。

输入输出不能互相覆盖，输出不能写入PDF归档目录或查询缓存。完整PDF、缓存、数据库、日志和提取输出只留本地tmp；裁切测试资源与来源记录随测试提交。


<a id="source-2026-10-15-stock-market-history-usage"></a>

## 2026-10-15 股票原始日线与成交额研究

> 来源：`docs/superpowers/specs/2026-10/2026-10-15-stock-market-history-usage.md`；整理前版本 `ad626dc`。


```powershell
stock-robot radar stock-market-window --calendar-db .stock_robot/radar_collector.db --as-of 2026-10-15 --output tmp/market-window.json
stock-robot radar stock-market-history --input companies.json --market-window tmp/market-window.json --as-of 2026-10-15 --cache-dir tmp/stock-history-cache --output tmp/stock-history.json
```

公司输入为非空companies对象列表或同样内容的JSON列表，每项symbol为沪深普通股票代码，支持任意候选集合，不固定20家或某个行业。窗口接受新envelope与原60日期列表。输入、窗口、输出不可重叠，也不能放在cache-dir内。该命令不改数据库或成员，不启用后台自动更新。

当前采用已实测的新浪原始历史日线。源变量必须匹配代码，只接受编码字符串赋值；不执行服务器返回的JS。复用已安装AkShare的可信解码常量，隔离子进程处理。价格保持未复权，成交额为来源原始元金额，不通过收盘价乘成交量估算。postVol/postAmt保留原字段，不自行合并。未复权价格用于原始行情和市值研究，不能直接作为总收益或复权趋势曲线。

每家公司保留实际URL、完整响应SHA256、采集时点和目标日原行。重复或无效记录标为缺失，停牌不填零、不ffill、不向更早交易日借行情。window_complete仅表示60日记录完整，不能当作公司或价格机会合格；顶层complete/partial仅表示采集执行状态。

只有60条目标日记录全部合法时才生成liquidity_fact。该对象可供后续显式证据添加及审核，始终是待审建议；它不批准准入、成长、价值或成员。disclosed_on记录实际采集的北京时间日期，不伪称历史公告日；采集晚于历史研究截点时不能当作当时可知事实。默认只导出，不自动保存或审核。

请求按已批准预算：串行、间隔1秒，单次15秒、最多重试2次，429停止该来源后续网络；无重定向、响应4MiB上限，必关闭响应。缓存保存完整原始响应，以内容摘要命名；刷新不会删除旧原文，命中仍重验摘要/身份并重新解析。失败不冒充缓存成功，缓存超过1GiB只提醒。完整下载响应和缓存只留本地tmp。

源接口与单位参考：[AkShare官方股票文档](https://akshare.akfamily.xyz/data/stock/stock.html)。本命令没有调用会合并流通股本并填充行情的stock_zh_a_daily封装。当前不推算全股类市值，不推算历史股本；股类/币种、复权与基准仍须独立接通。


<a id="source-2026-10-15-stock-market-window-usage"></a>

## 2026-10-15 股票研究的统一市场日期窗口

> 来源：`docs/superpowers/specs/2026-10/2026-10-15-stock-market-window-usage.md`；整理前版本 `ad626dc`。


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


<a id="source-2026-10-15-stock-official-roster-usage"></a>

## 2026-10-15 官方沪深证券候选名录

> 来源：`docs/superpowers/specs/2026-10/2026-10-15-stock-official-roster-usage.md`；整理前版本 `ad626dc`。


```powershell
stock-robot radar stock-universe-discover --as-of 2026-10-15 --cache-dir tmp/stock-roster-cache --output tmp/stock-roster.json
stock-robot radar stock-records-import --kind candidates --input tmp/stock-roster.json --db research.db --as-of 2026-10-15
```

第一条默认仅导出，不改研究库；第二条才显式导入共享候选，仍不审核或发布成员。扫描上交所主板、科创板和深交所A股表，不限既有20家或685家。三个来源的成功、失败和完整性分别保留，缺一个来源不能声称全市场完成。

返回companies是研究候选；上市日、来源行业代码/名称、原行、URL和响应摘要均可追踪。行业为名录原始标签，不冒充申万两级或行业龙头。issuer_type未知，不从公司名或金融业大类猜银行、证券或保险。深交所A/B股数只保留原文本，不把A股总股本/流通股本当作公司全股类股本，不据此推算市值。

普通A股范围明确排除689009存托凭证，单独保留范围排除记录；未知代码、重复、身份矛盾、分页不齐保留诊断。缺上市日为未知，不填默认年份。名录中的ST、未盈利等原始状态不被删除或改写为资格通过；经营、审计治理和龙头还需单独核验。

这些接口提供当前名录，不提供历史成分。当前北京日期可联网采集；历史日期仅复用当天抓取缓存，缺缓存返回部分结果，不以今天名单冒充历史。抓取时间不是交易所业务资料日期。security_proposal只是待审规范建议，默认不保存或审核，完整名录也不等于符合选股条件。

请求串行、间隔1秒，15秒总截止，最多重试2次，429按来源主机停止；SSE主板和科创共用主机限流，深交所独立。无重定向，原文4MiB上限；xlsx另有20MiB展开/64项/64列/20000行预算，错误dimension不会使22列丢失，公式不作为证券事实。生产解析同样隔离15秒，不增加依赖。

缓存使用内容摘要保留原始响应，命中仍校验并重解析，刷新不删除旧原文。超过1GiB仅提醒，统计失败不推翻已有结果。输出不能位于缓存内；实际数据、缓存和完整下载响应留本地，不自动推送、合并或发布。

来源：[上交所股票名录](https://www.sse.com.cn/assortment/stock/list/share/)、[深交所A股名录文件](https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110&TABKEY=tab1)。当前功能尚未提供全股类历史股本、公司总市值核验或行业龙头证据。


<a id="source-2026-10-15-stock-research-review-usage"></a>

## 2026-10-15 股票共享候选与证据审核使用说明

> 来源：`docs/superpowers/specs/2026-10/2026-10-15-stock-research-review-usage.md`；整理前版本 `ad626dc`。


日期2026-10-15。此入口把研究资料存进可持续审核的本地SQLite；候选不是正式成员，证据确认不是公司准入通过。本阶段没有成员发布命令，不改变现有ETF或空股票池页面。

### 导入与查看

使用项目.venv，在开发工作树需要设置PYTHONPATH为当前工作树src和根目录。db显式指定，可与现有radar.db共存stocks_前缀表；首次验收建议使用独立tmp数据库，不直接批准研究样本。

```powershell
stock-robot radar stock-records-import --kind candidates --input tmp/market-candidates.json --db tmp/stock-research.db --as-of 2026-10-14
stock-robot radar stock-records-import --kind review_packet --input tmp/dividend-workflow/review-packet.json --db tmp/stock-research.db --as-of 2026-10-14
stock-robot radar stock-records-list --db tmp/stock-research.db --search 长江 --page 1
stock-robot radar stock-records-list --db tmp/stock-research.db --symbol 600900 --output tmp/company-review.json
```

candidates接受公司对象列表或包含companies的对象；review_packet接受已有审核资料包。每条资料保留来源输入SHA、数据截点及原始字段，已有financial_verified等标志不赋予审核权。非有限原始金额保存为文字线索，不填零。所有候选admission_status仍pending_verification，不自动成为任何池成员。

重复同批导入幂等；新批补证与旧批留档共存。旧截点不会覆盖新截点公司展示资料；既有审核保留历史，不能把原文或字段变化后的新证据当成已审核。全库搜索默认每页15家，单公司详情包含证据ID、当前版本和审核历史。

### 补证与逐条审核

stock-evidence-add输入为：

```json
{
  "symbol": "600900",
  "kind": "financial",
  "payload": {"metric": "parent_net_profit", "fiscal_year": 2025, "amount_yuan": "需核对的原文金额", "scope": "reported_parent"},
  "source_url": "https://公司官方来源/原文.pdf",
  "disclosed_on": "2026-04-30",
  "content_sha256": "64位小写原文SHA256"
}
```

以上为格式说明，金额及hash占位符不能作为实际证据。应从真实留档材料复制来源、披露日、摘要及字段，不以每股分红或供应商总股本推算未明确的公司派息总额。

```powershell
stock-robot radar stock-evidence-add --input tmp/new-evidence.json --db tmp/stock-research.db --expected-revision 公司当前版本
stock-robot radar stock-evidence-review --db tmp/stock-research.db --symbol 600900 --evidence-id 证据ID --expected-revision 公司当前版本 --decision confirmed --reviewer 审核人 --reason 已核对的事实及口径
```

confirmed表示显式确认这一条原文事实；rejected表示驳回；needs_evidence表示仍需补证。确认必须有完整来源、披露日及原文摘要；只有供应商研究提示或失败文档不能空确认。每次审核保存审核人、理由、服务端时间和证据绑定，不覆盖旧记录。

每次写入使用公司revision校验；版本过期会拒绝，需重新查看最新资料，不能覆盖另一页面已经保存的审核。确认满90天显示expired；源披露日不能冒充审核日，接口恢复或重新导入不能刷新旧审核时间。

这些决定不会修改数值硬门槛，也不会将reported_parent自动变为company_ordinary。后续共同准入、风格及机会引擎仍须独立检查数据与规则；首批正式关注成员仍通过用户选择和发布形成。

### 数据安全与边界

SQLite使用stocks_专表和独立迁移版本，不更改ETF表或数据库全局user_version；写事务整批提交，错误资料不能半批留存。证据和审核长期保留，无自动删除。输入／导出文件不能覆盖数据库及其日志文件。

本阶段不采集新的行情、不发送外部通知、不注册自动任务；没有浏览器／数据库服务等新运行组件。真实审核资料可能包含过期资料或未核实口径，导入成功仅表示留档成功。


<a id="source-2026-10-16-stock-manual-updates-usage"></a>

## 2026-10-16 股票资料手动任务

> 来源：`docs/superpowers/specs/2026-10/2026-10-16-stock-manual-updates-usage.md`；整理前版本 `ad626dc`。


股票任务与ETF更新分别保存。自动更新保持关闭，启动CLI不会注册定时任务。当前可执行官方名录候选发现，以及既有成员的原始日线和股本采集。

### 命令

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

### 任务与产物

相同类型、池范围、日期和规则版本只有一个活动任务。整个股票队列同一时刻只执行一项；每次认领使用新租约令牌，90秒过期、15秒续租。过期后最多两次回队，第三次失败；续跑跳过已成功来源，并重新核对产物路径、长度及SHA256。已终结批次保持不变，再次入队创建新批次并复用采集缓存。

候选写入在同一SQLite事务内重验租约；旧执行者不能补写。已完成来源不可改写；失败/部分结果保留历史，最终状态按各来源最新结果计算。资料、审核和成员状态分别保存，采集任务不授予资格。

缓存与产物目录须互不包含，不得包含数据库、池配置、窗口输入或经过符号链接。单份结构化产物64MiB上限，恢复校验按64KiB块读取；原始来源仍遵守各适配器预算，缓存1GiB仅提醒、不自动删除。产物损坏时保留原文件并报告失败，不静默覆盖。

资料发现已接入真实来源；当前尚未提供网页任务入口，CLI运行结果和产物可用于后续页面联调。


<a id="source-2026-10-13-stock-radar-design-preview-validation"></a>

## 2026-10-13 股票池设计预览验证记录

> 来源：`docs/superpowers/plans/2026-10/2026-10-13-stock-radar-design-preview-validation.md`；整理前版本 `ad626dc`。


日期：2026-10-13。对象为独立设计预览，不是生产功能验收。对应[完整功能设计](../../specs/2026-10/2026-10-13-stock-radar-design.md#source-2026-10-13-stock-radar-functional-design)。

2026-10-14状态说明：用户先要求暂停预览讨论设计，后明确请求查看新预览。第1—6节截图与验证均属旧版历史；当前分层预览及未验证边界见第7节，不能用旧版结果替代新版验证。

### 1. 实际产物

- 正式方案：`../../specs/2026-10/2026-10-13-stock-radar-design.md#source-2026-10-13-stock-radar-functional-design`。
- 独立交互预览：`docs/superpowers/previews/2026-10/stock-radar/index.html`。
- 宽屏与窄屏截图：同目录`wide.jpg`、`narrow.jpg`。
- 生成及修正脚本：`tmp/stock-radar-market-20261013/build_functional_preview.py`及两份隔离修正脚本。

预览目录被忽略，不作为生产业务提交。预览复用原页面外壳、`app.css`与logo，复制样式只为提供独立目录；未修改原静态资源、正式股票名单或数据库。示例股票及全部成员状态均有永久示意标识，发布仅改变内存，刷新还原。

### 2. 环境与命令

运行`.venv/Scripts/python.exe tmp/stock-radar-market-20261013/build_functional_preview.py`生成预览；标准库HTTP服务器仅绑定127.0.0.1:8876，根目录限定为预览目录，公开预览和所需资源，不提供整个仓库。

最初在受限环境运行HTTP服务器，浏览器超时、PowerShell报本地套接字权限错误；停止后用批准的本地回环服务重启，页面加载成功。没有修改防火墙、代理或证书设置。

曾使用终端内联Python修改生成器，因PowerShell引号导致SyntaxError，未成功改文件；改为隔离脚本后生成成功。原失败未作为通过依据。

### 3. 浏览器验证结果

使用Codex内置浏览器操作本地预览，实际检查：

| 场景 | 结果 |
|---|---|
| 红利价值／成长趋势切换 | 名单与计数切换；长江电力分别显示红利和经营改善 |
| 正式／候选分离 | 待发布对象仅在候选；暂停成员保留在成员视图 |
| 勾选候选与变更清单 | 只对示例可入池对象提供勾选，清单显示新增对象，演示发布后进入示例成员 |
| 成员详情与证据编辑 | 共同准入、该池理由、机会、来源时点和历史分开呈现 |
| 未保存保护 | 编辑说明后关闭被阻止，提示保存或放弃；不会覆盖真实证据 |
| 行业筛选 | 选择银行只显示招商银行示例 |
| 首次空池 | 空状态展示，有效与暂停计数均为0；不会凑名单 |
| 更新中 | 显示阶段、保留旧列表、禁用更新按钮 |
| 取数失败 | 保留旧记录与日期，显示当前机会不可判断及重试路径 |
| 宽屏1280×900 | 一个h1，标题及首组件共用左侧对齐线，距主内容左边24px，无水平溢出，截图已查看 |
| 窄屏390×844 | 标题及首组件左边12px，控件换行，无水平溢出，详情和弹窗宽度有约束，截图已查看 |

发现并修正两个预览问题：双池示例原先混合展示理由，已按池拆开；空池原先汇总仍显示示例人数，已同步为0。修正后重新加载并复核受影响状态。浏览器临时视口覆盖已复位，交互预览标签作为交付保留。

### 4. 验证边界与下一步

本轮只验证设计预览交互与布局，不声明真正的采集、审核发布、任务续跑、数据库迁移、风险判断或ETF兼容已实现。没有运行项目pytest或全量门禁，也没有用此预览替代AGENTS要求的真实服务会话验收。

在聊天中呈现完整方案、推荐参数与预览，等待用户对具体方案确认；确认后再编写细粒度实施计划，在独立工作树测试先行实现，完成相关范围检查和真实服务验收。生产首次为空池，不能沿用示例成员。

### 5. 原指数／ETF入口遗漏的修正

用户反馈预览看不到指数池。原预览只列两个股票池，虽文字设计要求保留ETF，视觉预览未体现，属于设计遗漏。正式`config/radar_universes/`、ETF业务代码及数据未改动。

修正后共用选择器有两个分组、四个入口：原国内及港股ETF池、海外市场ETF池，加红利价值池、成长趋势池。ETF名称及全部22个成员只读取得自现有配置，国内及港股15家、海外7家；预览默认原国内池。ETF模式隐藏股票成员审核控件，保留更新数据、海外动态、评分依据、标的历史及池级策略参考入口，评分及曲线明确为未载入，不虚构原池业绩。股票模式恢复审核流程及自身日期。

浏览器实际切换海外池、成长池、国内池验证通过；海外7行、国内15行；股票审核控件按分支显隐正确，长江电力成长池理由仍为经营改善。修正截图为预览目录`index-pools-restored.jpg`。此验证仍是预览，不能称为生产ETF功能回归通过。

### 6. 按原界面收敛的紧凑预览

用户指出旧预览与原界面差距太大、下拉框不好看。当前`index.html`已被紧凑预览替换：使用`build_compact_preview.py`及`compact_preview.js`生成，复用原页面外壳、CSS和分组表格。旧截图与第3、5节交互结果仅保留为历史记录，不能作为当前版本全部功能通过的依据。

四个池改为直接可见的切换按钮，DOM中下拉框数量为0，支持选中状态和键盘左右切换。ETF保持原五列表格；股票以同样的紧凑表格按行业展示入池路径、状态与审核日期。取消大统计卡，候选和证据管理收进独立入口。详情在内容区展示并提供返回列表。

实际浏览器检查：国内及港股15行、海外7行；成长池详情打开并返回成功；键盘左移切至红利价值池且选中状态同步。1280×900标题及首组件左边296px、内容内部间距24px；390×844左边12px；两种尺寸均无水平溢出且仅一个可见h1。截图`compact-stock.jpg`与`compact-mobile.jpg`已查看，`compact-etf.jpg`记录原ETF布局。

新版管理入口只展示待审核候选与禁用发布控件，不声称新版完整发布、筛选、未保存保护或后台更新已验收。未修改生产页面、ETF配置、数据库或正式成员。仍等待视觉反馈与完整设计确认。

### 7. 2026-10-14分层设计预览

根据用户新请求生成`index.html`与`hierarchy-preview.js`，生成器为`tmp/stock-radar-market-20261013/build_hierarchy_preview.py`及`hierarchy_preview.js`，沿用原页面外壳、CSS与ETF配置。类型与池分别切换；股票默认行业概览，提供全部股票及全池搜索；行业内列表三列，详情关键指标留空且证据折叠；统一管理入口展示示例变更、候选和历史。包含空池与失败示例、更新进度和待核验候选禁用发布。所有操作仅影响内存，不调用生产API。

生成命令正常退出。浏览器绑定当前页面URL时被安全策略拒绝，未改用其他浏览器或间接方式绕过；本轮没有取得新版截图或完成真实交互、宽屏及390px窄屏检查。响应式规则已编写，但不能表述为实际布局通过。旧截图不用于展示新版。正式页面、名单与数据库未修改。

### 8. 池切换组件独立预览

用户反馈池切换扩展性不足，要求只预览可搜索的选择组件。新增忽略目录内`pool-switch.html`及`pool-switch.js`：固定当前池入口，弹出按类型分组的搜索面板；四个当前池与24个池扩容演示可切换，额外20个全部标为演示。选中后关闭面板，池名称与右侧操作同步；桌面面板内滚动，窄屏底部弹层。支持上下键、Enter和Esc，示例按钮不调用业务接口。

首次访问返回连接拒绝，旧本地预览服务器未运行；重启仅绑定127.0.0.1、根目录限定预览目录的标准库服务器后HTTP组件页成功打开。错误页是被工具拒绝访问的data页面，没有读取或操控该错误页；检查对象为重启服务后正常提供的HTTP组件页。

浏览器实际验证：四池分组与选中、切换股票后管理入口、24池面板内滚动、搜索“演示池 10”得到两项、搜索成长得到一项、Esc关闭、上下键与Enter选择。1280px无水平溢出；390×844弹层左右12px、宽366px、底部12px，无水平溢出。浏览器采集错误日志为空；临时视口已恢复。宽屏及窄屏截图为`pool-switch-wide.jpg`、`pool-switch-mobile.jpg`。仅组件预览，不构成正式页面验收或用户视觉确认；未修改正式资源、名单与数据库。文件检查及`git diff --check`通过。

用户随后确认将业务入口移出切换栏。当前组件切换栏只保留池选择与更新；ETF内容区显示“查看海外动态”，股票内容区显示“候选与成员管理”。更新脚本缓存版本为20261014-2。浏览器重载后验证ETF入口位置，切换红利价值池验证管理名称和切换栏仅两个可见按钮；390px无水平溢出，管理按钮位于内容区，视口已复位。截图为`pool-actions-wide.jpg`、`pool-actions-mobile.jpg`，旧组件截图保留历史。正式设计同步入口职责，`git diff --check`通过；仍为示意组件，未实现业务操作。

### 9. 完整预览接入池切换组件

用户确认组件调整后继续，将组件接入独立`index.html`，生成脚本`tmp/stock-radar-market-20261013/integrate_pool_selector.py`；不修改生产页面。移除类型与池两排按钮，改为一个当前池入口，类型在搜索面板中分组。切换栏仅更新，股票和ETF业务入口置于内容区。更新脚本缓存版本20261014-2。重新生成完整产物时按原生成器、分层生成器、接入脚本顺序运行，避免只运行旧生成器覆盖当前方案。

真实预览浏览器验证：搜索池并切换；国内及港股ETF仍有15行；成长池行业进入、股票详情返回保留电力设备行业、行业内搜索长江电力得到其他行业结果、清除搜索恢复原行业；管理打开并返回。仅一个h1、无原生select，宽屏及390px无水平溢出；窄屏选择面板左右12px、宽366px、底部12px；错误日志为空。截图`integrated-pool-wide.jpg`、`integrated-pool-mobile.jpg`已查看，临时视口复位。

仅验证上述示例交互与布局，不声称股票数据采集、审核发布或全站真实服务验收通过。资格、机会和成员状态是示意，关键财务指标未载入；没有新增正式名单、数据库迁移或业务代码。文档与生成脚本检查采用`git diff --check`和项目虚拟环境的`py_compile`，不运行业务pytest或全量门禁。


<a id="source-2026-10-13-stock-radar-market-candidates"></a>

## 2026-10-13 全市场初筛的685家研究候选

> 来源：`docs/superpowers/plans/2026-10/2026-10-13-stock-radar-market-candidates.md`；整理前版本 `ad626dc`。

研究日期：2026-10-13。来源：[全市场数值初筛报告](2026-10-14-stock-radar-implementation.md#source-2026-10-13-stock-radar-market-screening)。

这份名单由交易所名录与批量财报产生，按股票代码排列。全部尚未获得正式股票池资格；没有完成行业龙头、治理、现金流、分红、60日流动性及完整估值审核。五粮液有此前已发现的治理待核验事项。价格和市值是研究日来源快照，不是统一收盘价格。行业是已有本地映射，缺失必须后续核验。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-13-stock-radar-market-candidates.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-13-stock-radar-market-quality"></a>

## 2026-10-13 股票池行业与现金兑现研究报告

> 来源：`docs/superpowers/plans/2026-10/2026-10-13-stock-radar-market-quality.md`；整理前版本 `ad626dc`。

研究日：2026-10-13。范围：承接全市场初筛的685家候选，不改变正式池配置。本报告的“通过”只指拟议现金兑现线，不代表龙头、治理、风格或买点已经通过。

- 申万官方分类历史12,925条，最新更新2026-10-12 08:55；分类对照表解析511个代码。全部5,224只沪深普通A股与685家候选均可映射到一级、二级、三级行业。
- 候选分布在30个一级行业，一级供展示、二级供同业对比、三级用于细分业务核验。正式行业表未修改。
- 601家普通企业中，489家达到建议的三年现金兑现线，112家未达到；其余84家转入金融专项待核验。
- 财务接口标为银行、证券、保险的77家，加上申万非银金融但接口标为“通用”的7家，共84家。双重识别避免漏掉金融业务公司；最终仍需核验实际主营业务。
- 238家被标为“优先核验龙头证据”。这只是审核顺序，不是238家已认定龙头，也不是股票池容量。
- 所有685家正式入池资格仍为未确认。五粮液的既有治理待核验事项保留。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-13-stock-radar-market-quality.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-13-stock-radar-market-screening"></a>

## 2026-10-13 股票池全市场数值初筛报告

> 来源：`docs/superpowers/plans/2026-10/2026-10-13-stock-radar-market-screening.md`；整理前版本 `ad626dc`。

研究日期：2026-10-13，北京时间。状态：全市场候选发现的研究验证，不是正式股票池、龙头认证或买入名单。

起点是交易所当前名录，覆盖沪市主板、科创板、深市主板与创业板，未以原20家公司作为候选边界。批量取得行情和9个财务报告期，按已确认持续盈利条件与建议规模／上市年限作数值筛查。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-13-stock-radar-market-screening.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-13-stock-radar-market-signals-candidates"></a>

## 2026-10-13 优先候选行情估值明细

> 来源：`docs/superpowers/plans/2026-10/2026-10-13-stock-radar-market-signals-candidates.md`；整理前版本 `ad626dc`。

研究日2026-10-13，行情和估值截至2026-10-12。仅覆盖238家优先对象，其余447家未在本轮取数而非被淘汰。日频分位仍待行业及财务口径核验；均线数值不等于交易机会。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-13-stock-radar-market-signals-candidates.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-13-stock-radar-market-signals"></a>

## 2026-10-13 股票池行情估值与最新治理核验

> 来源：`docs/superpowers/plans/2026-10/2026-10-13-stock-radar-market-signals.md`；整理前版本 `ad626dc`。

研究日期：2026-10-13；行情及估值截点统一为2026-10-12。覆盖238家优先核验对象，承接685家全市场初筛候选；其他447家仍保留研究资格，本轮没有采集其行情估值，不视为排除。正式成员与正式机会仍未输出。

行情使用AkShare的东方财富历史日线，均线使用前复权收盘价，成交额使用接口原始成交金额。前轮缓存16家复用，本轮未从今天股价或股本伪造历史曲线。成交额字段与16家公司未复权缓存的960个交易日记录完全一致。两工作线程，每目标结束间隔0.5秒，请求超时12秒；238家无采集失败。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-13-stock-radar-market-signals.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-13-stock-radar-quality-candidates"></a>

## 2026-10-13 股票池行业与现金兑现候选明细

> 来源：`docs/superpowers/plans/2026-10/2026-10-13-stock-radar-quality-candidates.md`；整理前版本 `ad626dc`。

研究日：2026-10-13。全部685家仍待正式资格核验。行业均来自申万官方；现金线0.8仍为建议参数。排名为完整二级同业范围的名次／有数值同业数，不限于本表。

“优先”表示收入或来源市值前三且现金线通过／金融专项待核验，仅安排审核顺序；“其他”也可补证细分龙头。“增长提示”仅指TTM收入≥10%、扣非利润≥15%，未完成季度和趋势确认。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-13-stock-radar-quality-candidates.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-13-stock-radar-style-candidates"></a>

## 2026-10-13 股票池风格与风险候选明细

> 来源：`docs/superpowers/plans/2026-10/2026-10-13-stock-radar-style-candidates.md`；整理前版本 `ad626dc`。

研究日2026-10-13，全部685家均待完整准入审核。成长仅表示建议参数的数值条件，分红仅表示三年实施记录观察，审计仅为摘要。价值路径及当前机会仍未判断。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-13-stock-radar-style-candidates.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-13-stock-radar-style-risk-review"></a>

## 2026-10-13 股票池风格、审计与首批龙头证据研究

> 来源：`docs/superpowers/plans/2026-10/2026-10-13-stock-radar-style-risk-review.md`；整理前版本 `ad626dc`。

研究日：2026-10-13。范围：全市场初筛的685家候选。参数仍为讨论稿建议值，已确认的持续盈利要求保持不变。本轮没有发布正式成员，没有输出买入信号，也没有按已有20家固定名单选股。

若暂时只叠加普通企业现金兑现线、三年标准审计摘要并保留已知治理暂停，可优先继续研究的红利记录对象为455家、持续成长数值对象为133家、经营改善数值对象为128家。它们是后续审核队列，不是两个正式池；未通过龙头、完整治理、60日规模流动性、分红总额、估值和趋势审核。金融84家仍需专门财务质量核验，不能因为审计摘要齐全而视为普通企业条件已通过。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-13-stock-radar-style-risk-review.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-13-stock-radar-validation"></a>

## 2026-10-13 配置雷达股票池：首轮数据与20家样本核验

> 来源：`docs/superpowers/plans/2026-10/2026-10-13-stock-radar-validation.md`；整理前版本 `ad626dc`。

研究日期：2026-10-13（北京时间）。行情比较截止日：2026-10-12，避免把盘中价格混作完整日线。状态：研究验证报告，非正式股票池、非完整准入认证、非收益回测。

本轮已交付真实在线接口探针、20家跨12个申万一级行业的数值审核、行业来源核验、关键案例的法定披露交叉检查和规则调整建议。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-13-stock-radar-validation.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-14-stock-radar-annual-financial-evidence"></a>

## 2026-10-14 年报发现及财务字段建议实施计划

> 来源：`docs/superpowers/plans/2026-10/2026-10-14-stock-radar-annual-financial-evidence.md`；整理前版本 `ad626dc`。


日期2026-10-14。承接用户已确认共享财务证据、通用工作流及持续推进授权；不重新确认实现细节，不启用准入阈值。

### 范围与规则

扩展官方发现器支持annual_report模式，默认dividend保持兼容。年度报告关键词查询、完整报告与摘要/英文版区分、修订/更正风险保留，沿用精确公司身份、截点、分页、限流及留档规则。两种发现各用独立查询缓存目录。

新增年报解析模块与CLI `radar financial-extract`，读取留档PDF，先核对URL文件名、SHA/大小/PDF头。最多解析前20页的主要会计数据，不遍历数百页。复用25MiB文件限制，页数上限1000；超预算、扫描或不支持结构待核验，不放大下载限制。

支持明确证券代码、报告年度、人民币单位、连续三年表头的主要会计数据表。提取营业收入、归母净利润、扣非归母净利润、经营现金流建议，Decimal不浮点化；每条带年度、披露日、单位、来源、页码与摘录。标准无调整四列可对应当前/上年/前年；追溯调整或多列时只提取明确当前报告年度第一金额，比较年度不猜列映射，原始比较值保留待核验。负值保留，不以缺失当零；多章节同指标冲突不择优。

scope明确为reported_parent、consolidated等原文口径，不能自动改为company_ordinary。不推断无优先股/永续工具、不批准股类、不填coverage、profits或events，不从年度分红计划替代实际实施证据。financial_proposals全部requires_review=true、financial_verified=false。

工作流新增 `--annual-reports` 可选开关，默认不增加现有用户请求；开启后独立发现→留档→解析财务建议，审核包额外保留financial_documents/financial_proposals，规范证据及支付率研究仍只来自显式evidence。现有提取缓存仅适用分红，年报建议每轮从已核验原文提取；只解析前20页，网络仍复用留档。相关年度源失败明确计入partial，不影响其他公司和分红研究。无UI、数据库、自动任务或新依赖。

### 接口与执行分工

新增radar.stocks.financial_extract：`extract_annual_text(pages: list[str], *, symbol:str,source_url:str,disclosed_on:str,as_of:date)->dict[str,Any]`；`extract_archived_financials(archive_result:dict[str,Any],archive_dir:Path,as_of:date)->dict[str,Any]`，顶层items及financial_proposals，item含file_sha256/text_sha256/status/reasons及financial_proposals。

新增stock_robot.financial_documents CLI financial-extract。审核包build_review_packet新增可选关键字financial_extraction，保持旧调用兼容，绑定每项原文SHA并稳定标识。workflow run_workflow及CLI新增annual_reports bool，默认False；整体summary区分财务源和待核验。

测试先行，真实异常长江2025年度前页裁切保留调整前后表头。资源tests/resources/radar/cypc-600900-2025-annual-summary.txt，来源https://www.cypc.com.cn/cypc/attachDir/2026/05/2026051815345898336.pdf，采样2026-10-14，保留封面、第6页主要会计数据及第7页说明／季度数据的原始文本（PDF页索引0、5、6）；完整PDF留忽略tmp。新增资源在verify映射到财务模块及工作流测试。

财务模块及测试由独立实现代理执行；主会话接发现模式、审核包、工作流、CLI和映射。整阶段规格+质量双审查后复核。真实年报提取和实际annual模式查询验收，范围Ruff/Pyright/pytest，完整事项本地提交。所有建议仍不等于入池。

<a id="historical-2026-10-14-stock-radar-annual-financial-evidence-validation"></a>

### 年报发现与财务证据建议阶段验收

> 2026-11-24 文档整理：本节合并自 `2026-10-14-stock-radar-annual-financial-evidence-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期：2026-10-14。对应实施计划 `2026-10-14-stock-radar-implementation.md#source-2026-10-14-stock-radar-annual-financial-evidence`。用户已授权持续推进，不逐小部分确认；本阶段不启用正式筛选，不发布成员。

#### 实际改动

- 官方发现器增加annual_report模式，默认分红模式兼容；完整年报、摘要／英文／审议意见和修订／日期变更风险分开。工作流两种查询使用独立缓存。
- 增加财务前页解析、financial-extract CLI和dividend-workflow的可选--annual-reports。年报最大25MiB／1000页，有界读取25MiB+1字节后核验SHA、实际长度和PDF头，仅解析前20页同一已核验字节。
- 收入、归母净利润、扣非净利润和经营现金流输出建议，Decimal保留负值和单位；标准四列可对应三年，追溯多列仅当前报告年度。reported_parent不自动变为company_ordinary，不补股类或派息完整性。
- 审核资料包增加财务文档和稳定编号建议，沿用原文摘要、公司／来源／披露日绑定。建议永不自动写入规范profits、coverage或events。CLI、测试、验证映射、真实文本资源和使用说明一起收敛。

无新依赖、UI、数据库或自动任务。旧分红工作流默认不会额外请求年报。

#### 真实来源及CLI验收

长江电力600900，截止2026-10-14，真实巨潮年度报告查询范围完成，发现四个年度（2022—2025）报告及一份2022年度披露日期变更风险公告。原文URL／披露日／校验和在忽略目录的annual-discovery.json和annual-archived.json保留；旧查询起点因此还包含目标三年之前的报告，不把历史比较期建议当目标年度入池结论。

执行真实入口：

```powershell
stock-robot radar dividend-workflow --input tmp/annual-evidence-acceptance/companies.json --workspace tmp/annual-financial-workflow --as-of 2026-10-14 --annual-reports
stock-robot radar financial-extract --archive-result tmp/annual-financial-workflow/annual-archived.json --archive-dir tmp/annual-financial-workflow/annual-pdf-archive --output tmp/annual-evidence-acceptance/financial-cli.json --as-of 2026-10-14
```

最终四份年报模板匹配，输出4／4／4／12条建议，共24条；前三份对应2023—2025目标年度，各提取4项当前数据。旧2022标准表还包含2020／2021比较期；报告与建议的财政年度均保留，不混作当前三年。日期变更资料仍pending_verification、无财务建议。financial_verified=false、requires_review=true、rule_enabled=false；匹配不证明普通股股东口径或持续盈利准入通过。

无显式证据时：1家公司研究完整0、待核验1。显式提供此前已核验长江规范证据（从现有证据资源按公司筛选，原始来源字段保留）后：研究完整1、待核验0；这是旧支付率研究证据兼容结果，不来自本阶段机器财务建议自动放行。工作流总体partial继续反映风险资料及不支持分红文本。

最终续跑6份分红PDF、5份年报／风险PDF均cached；官方查询缓存复用。财务解析每轮核验原文并解析前页，没有声称财务提取缓存复用。完整PDF、查询响应、产物与日志都保留忽略tmp，不提交。初轮整PDF产生说明页误判的pending基线已记录在本阶段工具输出，修复后真实前20页不混入季度数字。

#### 审查与检查

独立规格、质量审查及修复复核通过。实际修复：说明／季度页误作主表；错误千分组被删逗号放大；缺失符号／游离负号／百分号／脚注被数字分词跳过；同页第二主表被忽略；首表缺收入时多表守卫遗漏其他金额行。异常行不猜值，多表整页待核验，正常章节标题和表头重复仍兼容。PDF stat后增长通过有界读取处理。

真实文本资源来自计划列出的官方公司PDF，采样2026-10-14，封面及PDF索引0／5／6原始文本包含追溯表头、说明与季度数字。`.gitattributes`仅对年报文本样本保留原始行尾空白，源码正常检查。初次年报模式测试先行确认缺少mode参数失败，再实现并验证原分红兼容。

最终命令：`.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`。17个暂存文件，Ruff通过、Pyright为0 errors／0 warnings／0 informations，272个相关pytest用例通过（30.31秒）。唯一测试临时目录已清理；补充本验收文档后仅做`git diff --cached --check`，复用同状态业务检查。没有运行全量回归或重复无关浏览器验收。

#### 尚未覆盖

仅测试一家公司实际年报链路，不代表全市场覆盖率；其他年报结构、扫描件、超预算文档和前20页之外的数据可能待核验。所有股类、普通股利润调整、年度分红完整证明、治理及行业龙头证据仍未全自动核实。正式数值准入、最新TTM／独立季度机会指标和成员生命周期未由本阶段启用。


<a id="source-2026-10-14-stock-radar-cash-threshold-experiment"></a>

## 2026-10-14 现金兑现门槛敏感性实验

> 来源：`docs/superpowers/plans/2026-10/2026-10-14-stock-radar-cash-threshold-experiment.md`；整理前版本 `ad626dc`。

实验日：2026-10-14。使用截至2026-10-13已保存的2023—2025年财务数据，未重新联网采集。

685家初筛候选中601家普通企业参加实验，84家金融企业专项待核验。结果不是全市场比例，也不是正式入池数量。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-14-stock-radar-cash-threshold-experiment.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-14-stock-radar-dividend-discovery-extraction"></a>

## 2026-10-14 官方公告发现与实施字段建议阶段计划

> 来源：`docs/superpowers/plans/2026-10/2026-10-14-stock-radar-dividend-discovery-extraction.md`；整理前版本 `ad626dc`。


日期：2026-10-14。承接已提交的支付率计算、旧缓存转换及原文留档阶段，用户同意继续公告自动发现和财务字段提取；正式数值筛选、成员发布仍关闭。阶段成果独立本地提交。

### 1. 官方接口事实与隔离探针

从巨潮官网公开 `common.js` 与全文检索页面的 `search_new.js` 查到股票搜索及公告查询路径，不执行网站脚本、设备指纹或验证逻辑。官网源码与响应保存于主仓库忽略目录 `tmp/dividend-discovery-probe-20261014/`。

- 股票搜索POST `https://www.cninfo.com.cn/new/information/topSearch/query`，keyWord=600900、maxNum=10；返回code、category、orgId、type、名称。实测精确A股600900、orgId=gssh0600900。机构ID必须查得且唯一，不拼接猜测。
- 公告POST `https://www.cninfo.com.cn/new/hisAnnouncement/query`，stock=代码与orgId、tabName=fulltext、column=sse或szse、pageSize=30、pageNum、searchkey、seDate及isHLtitle。官网脚本确认stock拼接和接口；其余字段通过单公司隔离请求验证。
- `searchkey=权益分派`、2024-01-01至2026-10-14实测返回5条长江实施公告，含三年年度与两次中期。返回totalAnnouncement=5、hasMore=false、totalpages=0，不能用totalpages判定分页；标题带em高亮，日期UTC毫秒，PDF为finalpage相对路径。

元数据来源以官网真实响应为准，不依赖第三方接口教程作正式规范。查询起点采用as_of年前第三年1月1日，允许发现同年中期派息，之后按公告实际财政年度核验。

### 2. 范围与接口

`announcement_discovery.py`：任意A股公司列表，精确匹配官方身份；分别查权益分派、利润分配两关键词，逐页保存原始响应，核对总数稳定、身份、公告ID与重复页，清理HTML标题并按北京时间确认截点。保留实施公告候选及相关变更风险公告，取消／更正等不能默默替代原文。PDF路径必须属于static.cninfo.com.cn的安全finalpage路径，不能接受任意绝对URL或路径穿越。

输出documents供既有archive命令直接留档；query_scope_complete只指两个关键词在给定截点的查询范围完整，绝不据此填财政年度分红coverage。空查询结果不证明没分红；失败保留部分发现和显式错误，429停止整个来源本轮后续查询。缓存按公司、截止日和请求字段独立、逐页断点记录，刷新显式重取。

`dividend_extract.py`：在核验PDF文件名／路径、SHA和PDF头后，用已有pypdf提取文本。只对明确实施公告模板提取字段建议，股票代码须匹配、财政年度需明确。金额使用Decimal和明确单位，本次实际金额、每股／实际基数、全年合计和已派中期分别保留，不把预案金额选作实施金额，不猜缺失股类或H股派息日期。扫描件、冲突、多种金额关系无法确认的文本转待核验。

输出event_proposals采用现有财政年度、amount_yuan、source_url、disclosed_on、share_class、paid_on等标准字段，额外标requires_review。全年合计没有完整派息日期证据时不填paid_on；全年合计不得再次叠加中期／末期。本阶段建议不直接写评估器events，不生成profits或coverage，不声称解析结果已经财务verified。

CLI新增 `radar dividend-discover`（input/output/as-of/cache-dir/refresh）和 `radar dividend-extract`（archive-result/archive-dir/output/as-of），现有研究／转换／留档命令不变。输入和输出分离，原文目录内部不得输出汇总，避免覆盖原始材料。所有产物financial_verified与rule_enabled=false。

### 3. 资源及异常策略

复用requests、pypdf，无新依赖、UI、数据库或自动任务。官方查询串行，源请求间隔1秒，仍不超过已确认并发2；连接／读取超时15秒，网络与5xx最多重试2，429不重试并停止源，不绕过HTML验证或重定向限制。缓存1GiB软上限只提醒，不自动删。

PDF金额字段建议不能作为“机器已核实”证据。元数据或原文来源失败、截点不一致、SHA错误、文件越界、异常PDF均单项隔离。即使模板匹配也保留审核状态；支付率门槛仍等待实验结论。

### 4. 任务与验证

1. 官方发现模块测试先行：精确身份、重复／冲突、HTML、时区、分页一致性、截点、429与重试、缓存续跑、PDF路径。
2. 实施建议模块测试先行：真实裁切的长江、美的、神华原始文本及构造冲突样本；金额不能取历史预案，回购后基数、ALL/A/H、未知股类、扫描、日期、SHA和路径边界。
3. CLI、验证映射、必要资源和使用说明接入。真实源全文保留tmp，离线必要样本统一tests/resources/radar，来源／采样日期／裁切口径在本计划及验收中记录。
4. S级规格／质量双审查，修复后复核。真实小样本通过“公司输入→发现→留档→字段建议”，第二次缓存续跑；与旧缓存和支付率工具不混淆。
5. 改动范围Ruff／Pyright／pytest、暂存差异检查，符合独立验收后本地提交。不运行全量或重复UI会话验收。

样本来源补充：2026-10-14采样。五份初始长江／神华／美的文本来自已有payout-evidence官方PDF；长江／神华裁至四、分配实施办法前，美的裁至三、分配对象前，保留历史方案误导与重字标题。新增中期／重要提示变体来自真实巨潮公告ID1224966232、1222342911、1224799414、1223787314、1224784190，披露日分别2026-02-05、2025-01-16、2025-11-12、2025-06-06、2025-11-04，来源https://static.cninfo.com.cn/finalpage/对应日期/公告ID.PDF；长江／神华同前裁切，美的裁至三、利润分配对象前。零命中资源cninfo-000333-announcements-empty.json保留官方完整小JSON，原始公告响应null格式是实际异常回归样本。明确金额关系容差0.01元只作保守展示舍入检查，大单位舍入差可转待核验，不推算缺失金额。

<a id="historical-2026-10-14-stock-radar-dividend-discovery-extraction-validation"></a>

### 分红公告发现与字段建议阶段验收

> 2026-11-24 文档整理：本节合并自 `2026-10-14-stock-radar-dividend-discovery-extraction-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期：2026-10-14。对应实施计划 `2026-10-14-stock-radar-implementation.md#source-2026-10-14-stock-radar-dividend-discovery-extraction`。仅完成资料自动化阶段，正式选股、股票池成员发布和自动更新仍关闭。

#### 实际改动

- 添加通用官方公告发现命令 `radar dividend-discover`：官方身份精确匹配、两关键词分页查询、原始响应缓存、断点记录、相关变更风险保留。
- 添加 `radar dividend-extract`：核验留档文件及校验和，从实施公告生成待审核字段建议；复用既有下载与支付率研究命令，不直接写入已核验财务证据。
- 接入 CLI、按范围验证映射、真实公告裁切文本和使用说明。没有新增依赖、UI、数据库或后台调度。

#### 真实来源验收

以长江电力600900、美的集团000333、中国神华601088，截止日2026-10-14，执行公司输入→官方发现→原文留档→字段建议。

- 三家公司关键词查询范围完成，分别发现6、5、3份公告，共14份；14份PDF留档成功。这不是财政年度分红完整性证明。
- 首轮解析5份匹配、9份待核验；补充通用中期及提示栏模板后，最终10份匹配、4份待核验，生成16条字段建议，全部保留 `requires_review=true`。
- `financial_verified=false`、`rule_enabled=false`。建议数量包含不同口径，不得直接相加为实际分红。
- 再次发现全部查询页复用缓存，三家公司仍完成查询范围。未运行全市场685家公司公告核验。
- 原始查询响应、14份PDF、各轮输出保留在忽略目录 `tmp/dividend-auto/`；官网脚本探针保留在主仓库 `tmp/dividend-discovery-probe-20261014/`。

#### 审查与回归

完成独立规格和质量审查，并复核修复。修复包括真实零结果 `announcements:null` 格式、发现与留档状态分离、从已核验字节解析PDF、修订公告标题、金额关系检查，以及回购后实际基数缺失或相互冲突时禁止使用总股本替代。

真实离线资源的来源、采样时间和裁切范围见实施计划。资源保留实际重字标题、历史预案段落和行尾空白；`.gitattributes` 仅对 `tests/resources/radar/*-implementation.txt` 豁免行尾及文件尾空白检查，源码仍执行正常格式检查。首次暂存格式门禁因这些原文空白及一处源码文件尾空行失败；清理源码空行、限定资源属性后复核通过。

最终命令：

```powershell
.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv
```

结果：23个暂存文件，Ruff通过，Pyright为0 errors、0 warnings、0 informations，165个相关pytest用例通过（32.89秒）。唯一测试临时目录运行结束后已清理。`git diff --cached --check` 通过。补充本验收文档后仅复核暂存差异格式，不重复业务测试。

#### 尚未覆盖的范围

扫描件、部分旧模板、复杂股类及日期证据不足仍为待核验；未自动生成归母利润、年度覆盖证明或完整A/H股分红证据。官方关键词查询范围完成不代表所有分红公告发现完整。未做全量回归；本阶段无前端或会话改动，复用此前真实UI验收，不重复启动服务。股票池正式准入标准与成员不因本阶段而改变。


<a id="source-2026-10-14-stock-radar-dividend-evidence"></a>

## 2026-10-14 普通企业分红实施证据与支付率补充实验

> 来源：`docs/superpowers/plans/2026-10/2026-10-14-stock-radar-dividend-evidence.md`；整理前版本 `ad626dc`。

研究日：2026-10-14。承接[支付率口径实验](2026-10-14-stock-radar-implementation.md#source-2026-10-14-stock-radar-dividend-payout-experiment)。目标为补充普通企业实际分红证据，不修改正式池，不启用支付率门槛。

长江电力600900三年实施公告及2025年年报已经取得。以各财政年度实际分红总额匹配2025年年报列示的同年度归母利润；年报含追溯调整，不作为历史时点选股结果。2024和2025实施公告已列全年含中期分红的合计，不再重复相加；GDR对应基础A股，不额外增加公司分红总额。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-14-stock-radar-dividend-evidence.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-14-stock-radar-dividend-materials"></a>

## 2026-10-14 通用分红资料准备与原文留档阶段计划

> 来源：`docs/superpowers/plans/2026-10/2026-10-14-stock-radar-dividend-materials.md`；整理前版本 `ad626dc`。


日期：2026-10-14。承接已提交的通用支付率研究基础阶段，用户已确认继续接入公告采集与缓存转换并要求独立成果及时本地提交。

### 范围与方案

新增缓存转换入口 `stock-robot radar dividend-prepare`：接受任意旧候选缓存及可选风格缓存，按symbol匹配，保留年度利润、每股分红等research_hints；实际金额、股类、年度完整性均不自动填为核实。可合并显式提供的标准化证据，但仍由上一阶段评估器校验。输出可直接交给dividend-research。来源路径与SHA256保存，不将旧supplier总股本乘每股金额当实际派息总额。

新增原文采集入口 `stock-robot radar dividend-archive` 与独立模块 `src/radar/stocks/source_archive.py`：接受明确URL公告清单，下载公开PDF并保存来源、SHA256、字节数、抓取时间、下载／缓存／失败状态及逐项checkpoint。来源URL发现与任意PDF财务金额抽取仍在后续适配器阶段，本阶段不声称已自动找出全市场全部实施公告。

输入 documents 每项有symbol、source_url，可另保留fiscal_year、kind、share_class等描述。同一URL只下载一次，不同公司描述仍保留。默认允许现有已核验官方来源精确主机名：www.cypc.com.cn、static.cninfo.com.cn、www.hkexnews.hk、static.sse.com.cn、www.sse.com.cn、disc.static.szse.cn；其他公司官网使用明确allow-host扩展。HTTPS、来源及每次重定向均验证；不接受凭据URL、IP或localhost，不绕过返回的验证页。

资源预算沿用已确认默认：最多并发2，每次连接／读取超时15秒（流式下载累计耗时可能超过15秒，不是整个文件总时长截止），网络异常与5xx最多重试2（总尝试3），429不重试并停止本轮该来源；已有在途最多2请求无法撤回，后续请求不得继续。缓存1GiB软上限只提醒，不自动删。PDF流式写入临时文件，校验PDF文件头后同目录原子替换；返回HTML即使HTTP200也失败。相同URL且原文SHA正确默认复用，refresh明确重取，失败保留正确旧原文并标明本次失败。

不新增依赖：requests已被现有radar数据源使用，复用共享环境。无数据库迁移、UI或后台自动任务。所有结果rule_enabled=false，原文downloaded不等于财务verified。

### 实施与验证

1. 测试先行：候选缓存转换（缺失、重复symbol、每股字段不伪造金额、显式证据匹配、输入来源摘要、不得覆盖输入）。
2. 网络独立模块测试：源与跳转限制、429停止、重试预算、HTML失败、SHA缓存及重启续跑、部分失败、刷新失败保留旧原文、并发上限、路径安全。
3. 接入CLI与定向验证映射，保留原有ETF及通用支付率命令，资源样本统一tests/resources/radar。
4. 按S级网络边界进行规格、质量双审查及修复后复核；原文真实下载小样本、完整685候选缓存转换＋研究串联验收。
5. 改动范围Ruff、Pyright、pytest和最终暂存差异检查。无UI／会话改动，不重复浏览器验收，不隐式执行全量。独立阶段本地提交，不推送或合并。

### 待后续完成

批量来源URL发现、不同公告结构抽取、真实派息基数／H股／回购证据标准化及事件完整性自动证明。不得因原文下载成功或线索转换成功，将685候选标成支付率合格或入池成员。

<a id="historical-2026-10-14-stock-radar-dividend-materials-validation"></a>

### 分红资料准备与原文留档阶段验收

> 2026-11-24 文档整理：本节合并自 `2026-10-14-stock-radar-dividend-materials-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期：2026-10-14。实施工作树 `C:/Users/25618/.codex/worktrees/stock-radar-pools/stock_robot`，分支codex/stock-radar-pools。承接通用评估阶段提交65673b2，按用户要求作为独立阶段本地提交，不推送、合并或发布。

#### 实际结果

- `dividend-prepare`将任意旧候选缓存、可选风格缓存与显式标准化证据按symbol组合，原始输入路径／SHA256可追溯。供应商利润及每股分红字段只保留线索，不猜实际金额、普通股口径、股类或事件完整性。
- `dividend-archive`按明确官方URL清单下载PDF，最多两个来源并发，同hostname串行处理429；15秒连接／读取超时，下载累计时长可能更长；网络与5xx共享最多2次重试预算。来源／跳转验证、PDF头检查、原子文件替换、逐项checkpoint、SHA缓存续跑及1GiB软上限提醒均实现。
- 失败隔离、刷新失败保留旧原文、输入／输出与归档目录分离，financial_verified与rule_enabled恒false。没有成员库写入或后台自动任务。

#### 真实验收

共享.venv及工作树PYTHONPATH下执行以下真实命令：

1. `radar dividend-prepare --candidates D:/code/stock_robot/tmp/stock-radar-market-20261013/market-candidates.json --style-cache D:/code/stock_robot/tmp/stock-radar-market-20261013/style-candidates.json --evidence tests/resources/radar/stock-dividend-evidence.json --output tmp/dividend-materials/prepared.json`：685家公司，保留2580条分红事件线索。
2. `radar dividend-research --input tmp/dividend-materials/prepared.json --output tmp/dividend-materials/research.json --as-of 2026-10-14`：完整1、待核验684。只显式长江样本提供实际金额证据；不是685家公司支付率通过率。
3. `radar dividend-archive --input tests/resources/radar/stock-dividend-documents.json --archive-dir tmp/dividend-materials/official-archive --output tmp/dividend-materials/archive-result.json`：三份长江电力2023—2025实施公告全部downloaded，字节数739950、218729、150126，记录原始／最终URL、SHA256和抓取时间。
4. 同一命令再次运行输出archive-resumed.json：三份均cached，SHA长度和final_url断言通过。输出financial_verified=false，未自动将原文转换为财务事实。

#### 检查及审查

最终10个源码／测试／资源／使用说明／计划暂存文件执行 `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff通过、Pyright零错误，115项pytest通过（25.20秒），成功测试目录由入口清理。之后仅添加本验收文档，提交前执行暂存差异空白检查；未再次重复业务测试。

S级规格、质量双审查及问题复核通过。修复缓存权限错误可中断批次、流式传输断开未共享重试、响应关闭异常误报成功文件失败等问题；另保留缓存最终URL、验证四个跨来源请求仍最多并发2及软缓存只提醒不删。相应回归纳入最终门禁。

静态修复中一轮第三方响应清理noqa未使用告警失败，调整为记录具体异常且注明隔离边界后通过；失败运行目录保留，没有将失败结果算成通过。未运行全量测试，无UI／会话改动，不重复浏览器验收。

#### 尚未完成

官方URL自动发现、任意PDF的分红金额／派息基数抽取、多股类与财政年度完整性自动核实仍需后续适配器。当前清单中的fiscal_year／kind／share_class是调用者描述，下载器不证明其财务含义。正式选股门槛、成员发布和自动更新未启用；原始PDF、大缓存及测试产物都留在忽略tmp。


<a id="source-2026-10-14-stock-radar-dividend-payout-experiment"></a>

## 2026-10-14 红利支付率口径实验

> 来源：`docs/superpowers/plans/2026-10/2026-10-14-stock-radar-dividend-payout-experiment.md`；整理前版本 `ad626dc`。

实验日：2026-10-14。用户同意先参考红利指数的年度支付率口径做实验，不等于批准启用数值门槛。

685家初筛候选中643家观察到三个财政年度连续实施现金分红记录，42家未确认连续；未形成记录不能直接判定没有分红。批量缓存包含每10股税前派息、实施状态、日期、总股本，但没有经核验的实际现金分红总额或实际派息基数。本次从该缓存核验完成的三年支付率数量为0，不输出685家的通过／失败统计。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-14-stock-radar-dividend-payout-experiment.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-14-stock-radar-dividend-workflow"></a>

## 2026-10-14 分红批量工作流及审核资料包实施计划

> 来源：`docs/superpowers/plans/2026-10/2026-10-14-stock-radar-dividend-workflow.md`；整理前版本 `ad626dc`。


日期：2026-10-14。承接已确认共享资料、可审计证据与通用脚本计划。用户要求持续推进，不逐小部分请求确认；本阶段只串联现有研究工具并整理核验资料，不启用数值准入或发布成员。

### 设计与执行范围

新增 `radar dividend-workflow --input 公司JSON --workspace 独立目录 --as-of YYYY-MM-DD [--evidence 已规范证据JSON] [--refresh]`。

一次命令执行官方发现、原文留档、字段提取、规范证据准备和支付率研究，保存独立阶段产物与最终摘要。复用现有采集器的限流、超时、重试、缓存软上限和429停止策略，无新依赖或后台调度。输入、可选证据文件必须位于工作目录外；汇总不能覆盖输入。工作目录使用明确版本及截止日、公司输入绑定，已有不同输入不能静默覆盖，应换目录。证据可更新并记录本次SHA，刷新显式重取官方数据。

始终执行发现与留档的原有缓存核验，失败阶段不被历史成功摘要掩盖。提取阶段可以复用绑定归档元数据、文件校验和及解析版本的完整结果；损坏、丢失或版本变化重新提取。每步原子写入，异常保留已完成产物和失败检查点。没有数据的公司继续进入资料包和研究，不能从统计中消失。

新增纯函数 `build_review_packet(companies, extraction, prepared, research, as_of)`，逐公司输出三个完整年度的核验清单、全部建议及稳定ID、原文SHA／摘录／公告日、现有规范证据和研究缺失原因。proposal ID按公司和完整建议内容确定，不依赖输入顺序；原文来源和SHA随建议保留。建议永不自动写入events/profits/coverage；既有规范证据只通过prepare_candidates明确合并。完整查询不替代完整年度证明，matched_template不代表审核通过。支持无分红、A/H缺失、无法解析原文和单公司失败，未知不填零。

最终摘要区分查询范围完成、下载与失败、模板匹配与待核验、研究完整与缺失。输出 `rule_enabled=false` 和明确research_only，不声称全市场筛选已完成。工作目录外原输入与PDF原文不会被改写。

### 任务、边界与验证

1. 主会话实现审核资料包模块及测试，覆盖建议稳定ID、重复去重、不同公司／股类、孤立建议拒绝、缺失研究、原文追踪、不填零和不改变输入。
2. 独立代理实现工作流模块与测试；现有CLI入口和verify映射由主会话集成。代理不得改变现有金融计算／发现／留档／提取模块。断点摘要失败、输入／目录冲突、缓存损坏、截点变化、可选证据变更为主要回归。
3. 整阶段独立规格与质量审查，修复后复核。使用已有三家公司原始缓存及PDF完成真实工作流，重复运行验证续跑，显式检查两股票池没有新增成员。
4. 按改动范围执行Ruff、Pyright、pytest及暂存差异检查；无UI修改，不重复浏览器验收。源代码、测试和正式说明一起本地提交，不提交缓存、原文或截图。

### 接口约定

审核包：`build_review_packet(companies: list[dict[str, Any]], extraction: dict[str, Any], prepared: dict[str, Any], research: dict[str, Any], as_of: date) -> dict[str, Any]`，位于 `radar.stocks.dividend_review`。顶层companies列表，每行symbol/name/years/proposals/evidence/research_status/unresolved；顶层requires_review=true、rule_enabled=false。建议ID使用canonical JSON SHA256，包含symbol及proposal内容；提取项file_sha256/text_sha256独立引用，不把平铺列表当唯一来源。

工作流函数：`run_workflow(input_path: Path, workspace: Path, as_of: date, evidence_path: Path | None = None, refresh: bool = False) -> dict[str, Any]`，位于 `stock_robot.dividend_workflow`。CLI定义同模块；复用 `_read`、`_write_json`、`prepare_candidates`、`run_batch`、`discover_announcements`、`collect_documents`、`extract_archived_documents`，最终调用审核包函数。模块常量版本纳入提取缓存依赖。主会话随后注册CLI。

补充验收边界：审核建议必须具有有效的原文和文本SHA；缺失、错误状态或非有限追踪字段逐项隔离，其他公司的资料仍保留。证据字段列表类型在任何采集和工作目录写入前校验。工作流摘要替换遇到PermissionError仅局部最多重试两次，等待50ms、100ms；持续失败记录错误，不修改ACL或绕过权限。

<a id="historical-2026-10-14-stock-radar-dividend-workflow-validation"></a>

### 分红批量工作流与审核包验收

> 2026-11-24 文档整理：本节合并自 `2026-10-14-stock-radar-dividend-workflow-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期：2026-10-14。用户要求持续推进，不逐小部分确认；承接已有共享资料与通用研究工具方案。对应实施计划为 `2026-10-14-stock-radar-implementation.md#source-2026-10-14-stock-radar-dividend-workflow`。

#### 完成范围

新增 `radar dividend-workflow`：一次命令执行官方发现、PDF留档、字段提取、规范证据准备、支付率研究及审核资料包。工作目录绑定公司输入SHA、截止日和版本；显式证据可更新并重算，错误格式在采集和写入前拒绝。各阶段原子写入、异常检查点及缓存依赖明确。

新增 `build_review_packet`：保留所有公司、失败公告、三个完整财政年度缺口、规范证据及带稳定ID的机器建议；原文／文本SHA、来源、披露日和摘录可追踪。机器建议没有审核放行权，不自动填利润、股类范围、实际分红或年度完整性。坏原文追踪字段逐项隔离，不丢失其他正常公司的建议。CLI、有限验证映射及使用说明一并接入。

没有新增依赖、前端、数据库、成员发布或自动调度。红利价值池与成长趋势池仍revision=0、items=[]、status=empty；研究结果不能代替正式入池。

#### 真实CLI验收

使用已有三公司输入（600900、000333、601088），从空目录 `tmp/dividend-workflow-acceptance` 执行真实CLI路径，截止2026-10-14。

- 三家公司查询范围完成，14份PDF下载，10份模板匹配、4份待核验，16条审核建议。
- 未提供规范证据：研究完整0家、待核验3家。公司不因缺少建议或财务证据而从产物中消失。
- 显式提供此前核验的规范证据资源：长江电力研究完整，另两家待核验，即完整1家、待核验2家。结果只表示支付率研究资料完整，不表示共同准入或投资建议。
- 最终重复执行：14份PDF缓存复用，提取缓存复用，3家公司支付率结果缓存复用。补证来源SHA保留，审核包始终requires_review=true、financial_verified=false、rule_enabled=false；工作流总体partial反映仍有待核验内容。
- 原始产物、无补证及补证摘要快照都保留在忽略tmp；不提交PDF／缓存／日志。

首次真实续跑出现一次Windows PermissionError（WinError5）拒绝替换summary.json，随后两次成功。针对可复核异常，在本工作流写入处加入最多两次局部重试（50ms、100ms），持续拒绝仍报错并尝试记录失败检查点，不修改ACL；两条精准回归及最终真实续跑通过。原有通用原子写入器未改动。

#### 审查、检查与失败记录

独立规格与质量审查均已复核通过。修复缓存准备／证据落盘阶段外异常缺少失败检查点、证据列表校验过晚、原文SHA缺失和非法状态／非有限追踪字段等问题。最后的局部文件占用修复由主会话差异核对与精准测试复核。

最终范围命令：

```powershell
.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv
```

源码Ruff通过，Pyright为0 errors、0 warnings、0 informations；相关测试150项通过，1项新增验证映射测试失败。失败原因是测试用PowerShell -File传递两个Files参数被错误绑定为Scope；改为分别参数化验证后，只复核这两个用例及测试文件Ruff／Pyright，2项通过。其他150项同一代码状态结果复用，最终相关回归合计152项通过。`git diff --cached --check`通过。不执行全量回归。

先前失败已保留：一次主会话遗漏创建临时目录导致19项setup错误（18项审核包测试已通过），随后新独立目录重跑19项通过；首次门禁RUF100与项目显式异常标注要求冲突，局部豁免unused标注后通过；随后SHA校验与故意异常测试样本的类型收窄错误已修复。失败目录留tmp用于诊断，未把未执行的业务断言算作通过。

#### 未完成范围

目前规范证据仍需显式提供；任意年报利润与普通股口径、所有股类及完整年度分红证明尚未全自动核验。官方关键词完整范围不等于年度事件完整。没有全市场公司自动财务核验或正式数值筛选；未知保持待核验。无UI／会话变更，沿用此前真实服务验收，未重复浏览器测试。


<a id="source-2026-10-14-stock-radar-generic-dividend-research"></a>

## 2026-10-14 通用分红支付率研究基础阶段实施计划

> 来源：`docs/superpowers/plans/2026-10/2026-10-14-stock-radar-generic-dividend-research.md`；整理前版本 `ad626dc`。


日期：2026-10-14。用户已确认通用化任意候选列表、按年度汇总去重、多股类核验、缺失待核验、缓存续跑及复用统一模块的方案，并要求有独立成果即做本地提交。本阶段是已确认方案的可独立验收基础，不启用尚待确认的支付率或股息率门槛。

### 1. 范围与设计

采用无网络的证据标准化入口及确定性评估器：`src/radar/stocks/dividend_payout.py`；批量入口 `src/stock_robot/dividend_research.py`，CLI `stock-robot radar dividend-research`，脚本 `scripts/research-stock-dividends.py`调用同一模块。已有独立工作树继续复用，无新增依赖、数据库迁移、页面或任务调度。

输入JSON为 `{"companies": [...]}`。每家公司提供symbol、name、required_share_classes（A或A/H等所需普通股股类）；profits按年度记录amount_yuan、CNY、company_ordinary口径、source_url、disclosed_on；coverage提供year、complete及证明来源／公告日；events提供event_id、fiscal_year、share_class（A、H或ALL）、amount_yuan、CNY、来源／公告日与paid_on。公司列表重复symbol拒绝，单家公司财务证据不全返回待核验。

最近三年按显式as_of的前三个完整财政年度确定，金额用Decimal，来源明确的已实施税前现金分红匹配同年度普通股利润。每年事件需明确补齐，不根据“看见几次分红”猜完整；ALL与分股类不能重复汇总，事件标识相同金额冲突不能静默取最后一条。晚于截点、负分红、非有限金额、非正利润、口径／币种／股类不匹配均待核验。

输出年度金额和支付率、三年年度比率算术平均、累计金额比率及中证红利参考条件判断，complete仅指可完成本项计算，不代表公司入池。完整但不符合参考条件为false；证据不足为null。所有结果rule_enabled=false。金融企业可研究普通股支付率，但不套用工业企业现金门槛，不判断金融准入。

缓存可选、按公司独立保存，键包含完整输入、研究日和计算版本；缓存摘要校验失败重新计算。证据或研究日变化自动失效；同目录临时文件原子替换保证不中途损坏旧结果，输入和输出相同路径拒绝。缓存无自动清理，无限流／重试参数，因为本阶段不发网络请求。

### 2. 执行任务

1. A级计算模块：测试先行，核验真实长江金额、民生普通股口径、支付率两公式反例、完整性、重复、A/H、ALL冲突、截点及100%边界。
2. CLI及脚本：任意公司对象列表、独立缓存、无配置数据库副作用、坏批次保留旧产物、注册到现有radar组。
3. 样本与使用说明：正式离线资源放tests/resources/radar；原始PDF和大量缓存仍在忽略tmp。输入示例须区分官方原始金额与构造边界数据。
4. 验证映射：新模块和脚本映射到相关测试，避免默认跑整个radar目录；CLI改动复核CLI帮助和原有行为。
5. 合并规格／质量单审查，收敛修复，按改动验证后将本阶段源码、测试、规格与研究文档作为独立本地提交，不推送、合并。

### 3. 接口探针与来源事实

当前项目Pydantic、click与Python3.11均已提供，无需新增运行组件。开发工作树无.codegraph；主仓库定位CLI时已调用CodeGraph，工作树源码直接读。主仓库共享虚拟环境需PYTHONPATH显式指向工作树src及根目录。

公司实施公告说明金额必须排除回购股份、保留多股类、区分中期与年度；完整证据实验见2026-10-14-stock-radar-implementation.md#source-2026-10-14-stock-radar-dividend-evidence。批量旧缓存不能证明实际派息总额；本阶段没有宣称自动抓取685家公司实施公告或自动解析任意PDF金额。

### 4. 验证与后续

运行改动范围Ruff、Pyright、pytest（共享.venv、唯一项目内临时目录），真实执行批量命令及缓存续跑，检查输出计数、截点和正式规则关闭。无需浏览器会话验收，因为没有UI／会话改动。不运行全量门禁。

后续独立阶段接入采集适配器、原始材料来源索引与候选缓存转换；来源失败、429及资源预算遵循已确认设计。拿到足够完整样本后再确定支付率门槛。当前执行计划不授权把人工补证视为自动核实，也不授权发布股票池成员。


<a id="source-2026-10-14-stock-radar-generic-dividend-validation"></a>

## 2026-10-14 通用分红研究基础阶段验收

> 来源：`docs/superpowers/plans/2026-10/2026-10-14-stock-radar-generic-dividend-validation.md`；整理前版本 `ad626dc`。


日期：2026-10-14。工作树：`C:/Users/25618/.codex/worktrees/stock-radar-pools/stock_robot`，分支`codex/stock-radar-pools`。用户要求阶段成果及时做本地提交，本次不推送、合并或发布。

### 实际交付

- 通用离线证据评估模块：三年按财政年度汇总，利润／分红普通股口径、金额、来源、日期、年度完整性与股类核验；Decimal精确计算两种支付率，参考条件仅作实验。
- 任意候选列表的CLI及同实现脚本；每家公司独立缓存，证据／截点／版本变化重新计算；损坏缓存重建，非有限金额作为无效证据保留文本，不使其他公司中断。
- 全年合计与中期／末期、公司总额与A/H分项冲突防重复；100%边界、财政年度日期下限；允许后报证明此前已派息。
- 正式样本、相关测试、验证映射、使用说明，连同当前已确认风格现金流／连续分红规则和相关研究文档收敛提交。

### 验证命令与结果

共享环境为 `D:/code/stock_robot/.venv`，源码导入指向工作树src及根目录。

1. `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：17个暂存文件的初版定向门禁，Ruff通过、Pyright零错误，134项pytest通过（23.14秒）。成功运行目录已由验证入口清理。
2. 在上述结果之后，审查指出财政年度日期与A/H金额覆盖元数据两项风险，并额外修复NaN证据输出隔离。最终对四个受影响源码／测试文件执行 `verify.ps1 -Scope Changed -Files ... -VenvPath ...`：Ruff通过、Pyright零错误，58项测试通过（0.56秒）。此前未改变的CLI兼容与验证入口测试结果复用，不将两轮数字相加冒充单次测试总数。
3. 真实CLI：`stock-robot radar dividend-research --input tests/resources/radar/stock-dividend-evidence.json --output tmp/dividend-research/results.json --as-of 2026-10-14 --cache-dir tmp/dividend-research/cache`：3家，完整1、待核验2；脚本入口运行一致，复用3家缓存。
4. 完整685候选列表离线批量：仅长江公司导入已核验证据，其余只提供代码与名称。结果完整1、待核验684；第二次执行复用685家缓存。计数和rule_enabled=false断言通过。该结果不是685家的财务支付率实测，也不是通过率。
5. A级合并规格／质量审查及针对问题复审通过。最终提交前补暂存差异空白检查。未执行全量回归；无UI／会话改动，不重复浏览器验收。

### 异常记录与限制

CLI接入首次文本替换误改函数内部导入缩进，静态检查检出；从本阶段开始前的文件恢复后，仅保留顶层导入和注册三行差异。非有限金额修复复核首次导入排序因PYTHONPATH环境不一致失败，统一源码导入环境后通过；失败检查目录保留，未作为测试成功。

当前工具信任输入证据，不自动访问source_url或证明PDF真实。覆盖声明必须由采集适配器／人工核验正确提供；未标明金额范围及事件覆盖关系的重叠不可能全部自动识别。meets_reference_condition只判支付率均值及最近年，不替代连续三年分红、现金质量、治理、龙头或金融审核。

尚未交付全市场实施公告采集、原始供应商缓存转换或自动联网恢复；股票池成员仍为空，正式筛选及自动更新仍关闭。大量原始PDF、研究缓存与smoke产物均保留于忽略tmp，不提交。


<a id="source-2026-10-14-stock-radar-liquidity-research"></a>

## 2026-10-14 股票池流动性门槛敏感性研究

> 来源：`docs/superpowers/plans/2026-10/2026-10-14-stock-radar-liquidity-research.md`；整理前版本 `ad626dc`。

日期：2026-10-14。用户暂未接受近60日成交额1亿元共同硬门槛，要求核对数量及低迷市场适用性。本轮只研究，不启用筛选或修改正式成员。

复核2026-10-12截点的238家优先研究对象及固定60个沪深市场交易日，原始成交额均值：≥3000万元238家、≥5000万元238家、≥1亿元236家、≥2亿元213家。苏泊尔0.887亿元、晨光股份0.843亿元低于1亿元。这批对象来自685初筛候选中的优先核验队列，有规模和活跃度偏向，不代表全A股或全部685家，也不等于审核完成的龙头。

研究结论、口径与数量摘要见本计划原有第 4—5 节；不代表正式成员资格或当前通过率。完整原记录保存在本地 `tmp/document-consolidation-october-20261124/docs/superpowers/plans/2026-10/2026-10-14-stock-radar-liquidity-research.md`，亦可用整理前 Git 版本 `ad626dc` 与来源路径恢复。


<a id="source-2026-10-14-stock-radar-stage1-validation"></a>

## 2026-10-14 股票池第一阶段实现与验证记录

> 来源：`docs/superpowers/plans/2026-10/2026-10-14-stock-radar-stage1-validation.md`；整理前版本 `ad626dc`。


日期：2026-10-14。分支：`codex/stock-radar-pools`。工作树：`C:/Users/25618/.codex/worktrees/stock-radar-pools/stock_robot`。依据[实施计划](2026-10-14-stock-radar-implementation.md)，仅第一阶段；股票正式筛选及完整成员管理未实施。

### 1. 实际改动

统一只读池目录`GET /api/v1/radar/pools`组合原两个ETF池和独立股票定义；新增`config/radar_stock_pools/`，股票ID为`dividend_value`和`growth_trend`。原ETF非空模型及四因子不放宽。股票定义与空成员接口独立，规则为未配置，更新拒绝且不新建任务。

正式选择组件显示当前池，按类型分组搜索；桌面下方搜索面板、窄屏底部弹层、键盘与焦点返回。池用途说明最多两行，完整内容仍参与搜索。切换栏仅选择和更新，海外动态移入ETF内容区，候选与成员管理位于股票内容区。股票池显示真实空态与暂不可用原因，不导入示例或研究名单。

配置驱动目录、低排序新池默认国内ETF优先、禁用与ID冲突校验、合法constructor池名、迟到响应、历史快照所属池与延迟初始化都有回归。缓存版本更新为20261014-pools-2。验证脚本补股票配置与组件定向测试映射。

### 2. 审查与修复

实施由后端、前端子代理按S级任务分工，独立规格与质量双审查，修复后双复审通过。修复：默认池依赖排序、跨池历史快照错配、异步关闭后的焦点丢失、普通对象继承属性与合法池ID冲突、会话初始化延迟时雷达空白。初次浏览器确实遇到导航加载时序空白，后续修复验证能正常进入。

### 3. 检查命令及结果

共享主仓库`.venv`，`PYTHONPATH`同时设置工作树`src`与根，探针确认`api.app`、`radar.universe`、`stock_robot.cli`均导入工作树。

最终相关pytest命令：

```powershell
D:/code/stock_robot/.venv/Scripts/python.exe -m pytest tests/api/test_radar_pools_ui.py tests/api/test_static.py tests/api/test_radar_update_ui.py tests/api/test_radar_pools.py tests/radar/test_pool_catalog.py tests/radar/test_universe.py tests/api/test_radar.py tests/test_verify_script.py -q --basetemp=<唯一运行目录>/basetemp -o cache_dir=<同运行目录>/cache
```

结果：107通过，23.33秒；一个既有Starlette/httpx弃用提示，不修改依赖。先行红态验证了配置及组件漏检映射，修复后通过。初次临时目录未建立导致一个fixture错误，已创建唯一父目录并单独复核通过，未把首轮错误说成测试通过。

Ruff对新增领域与API代码及相关测试全部通过。定向Pyright使用忽略目录内临时配置显式绑定工作树与共享虚拟环境；先检查源码通过，测试模块导入根缺失导致一项诊断，补工作树根后仅复核受影响测试，0错误0警告。JS语法检查由前端实施代理完成；`git diff --check`通过。未运行全量门禁。

### 4. 真实服务验证

用工作树`PYTHONPATH`执行项目规定启动路径`D:/code/stock_robot/.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765`。真实HTTP界面和API验收，不是独立preview。

原项目公开ETF快照、完成回测产物、本地行情和交易日历只读复制到隔离验收目录。不复制原任务队列或自动启动设置，不覆写主库；原快照国内6批、海外5批完成。第一轮隔离环境没有模型配置，仅有会话回退结果；随后仅在忽略的本地状态复用现有模型设置并重启，真实发送“请只回复：已收到。”后出现完整正文“已收到。”，思考与正文分开，返回原会话能读取。凭据不输出、不提交。

浏览器实际验证：

- 搜索、两ETF和两股票池切换；国内15、海外7个真实快照成员。
- 股票成员0，更新与准备候选禁用，显示规则／采集未配置；管理入口说明暂不可用。
- 键盘选池后焦点为新`radar-pool-trigger`，地址切换为对应池。
- ETF真实详情、历史快照与返回；单标的表现和池级策略分开，成本、警告及该ETF六笔成交记录保留。
- 历史详情510300、截点2026-09-16的曲线鼠标节点：2019-07-18，同日沪深300ETF89.6%、沪深30044.1%；纵轴收益率、图例和区间控件可见，区间控件位于横轴下方。
- 构造国内池地址包含海外历史run，显示所属池不一致错误、无列表渲染。
- 内容区海外动态入口打开真实更新结果窗口；人工更新显示实际准备日历等进度，切到股票后其能力仍禁用、没有串池。该次最终行情采集结果仍待完成，不据此声称最新行情全部通过。

全站1280×900与390×844测量覆盖个股、指数、雷达、报告库、订阅、日志、配置。宽屏侧栏当前宽210px，标题与可见首组件左边234px，内容区内24px；标题y82px，顶部栏58px后24px。窄屏左边12px、标题y70px、顶部栏54px后16px。没有水平溢出。报告库及窄屏配置内容因必要滚动条占位约少15px，保留滚动条。初次批量导航时侧栏动画尚未完成导致测到上一视图，原测量未采用；最终用正式深链接和实际可见元素重新测量七页，记录保存于忽略目录。

真实股票池390px面板左右12px、宽366px、底部12px；查看实际宽窄屏与图表截图。截图、测量JSON、缓存和临时类型配置均留`tmp/stock-radar-market-20261013/`及工作树忽略目录，不提交。视口复位。

### 5. 交付边界

第一阶段可用，完整股票筛选、真实行业成员列表、证据编辑、发布与生命周期、股票后台采集尚未实现。用户已选择先逐项讨论数值门槛，暂不启用正式筛选；自动股票更新仍关闭。资源及展示预算已确认但未因此注册任务或启用新采集。

本地提交不含推送、合并、发布；主`dev`检出不覆写。本任务的独立工作树服务可供查看。下一阶段数值准入须待门槛讨论，不以界面确认代替规则确认。


<a id="source-2026-10-15-stock-radar-approved-styles"></a>

## 2026-10-15 两池已确认风格条件实施计划

> 来源：`docs/superpowers/plans/2026-10/2026-10-15-stock-radar-approved-styles.md`；整理前版本 `ad626dc`。


日期：2026-10-15。承接共同准入提交10ad057及已确认完整股票池方案。用户已授权持续执行、不逐小项确认。本次沿用design-before-implementation技能执行已确认规则，不批准未确认阈值，不新增运行依赖、不改UI。

### 范围与接口

新增radar.stocks.style.evaluate_stock_styles(symbol,evidence,as_of,*,market_window=None)，返回共同准入和dividend_value/growth_trend两个独立研究结果。新增stock-style-evaluate CLI，复用stock_admission.evaluate_store的来源修订校验、单公司隔离、文件保护、JSON输出。原stock-admission-evaluate行为保持不变。所有计算只消费StockResearchStore已审核证据，不改成员，不写最终公司资格，不隐式自动更新。

共享源验证复用admission._groups，将kind前缀及名称参数化，默认原七组不变；数值、日期验证复用现有纯函数。金额求和使用dividend_payout._sum_amounts扩展Decimal精度；比较用交叉相乘，不依赖四舍五入后的显示比率。研究市场窗口仍只是研究上下文，不能用作正式发布。

### 规范事实

七组admission_*仍按已有约定。新增style_*仅消费显式审核、归属一致、来源完整、截点有效、审核90天内且不冲突的组：

- style_cash_flow：issuer_type=ordinary、currency=CNY、scope=company_consolidated、parent_profit_scope_reconciled=true；annual恰为最近三个完整年度，每行year、operating_cash_flow_yuan、disclosed_on。分母只能来自同公司已审核admission_profitability中的三个完整年度parent_profit_yuan，currency=CNY、scope=company_parent、每年严格正值且披露在年度结束后/截点前。不得另填一个有利分母，不能把经营现金流负当利润负。缺失或重复年度、口径未核实均不计算阈值。
- style_cash_review：issuer_type、verdict=clear/failed/pending、basis非空。普通企业review核验现金恶化、应收存货、资本开支债务及支撑；金融与已审核security issuer_type一致，专项核验，不套0.8。普通企业数值阈值passed不替代专项review。成长cash review的非clear仅提示继续核验，不依据未确认量化风险线自动淘汰；共同财务专项硬失败仍由共同准入处理。
- style_dividend_history：currency=CNY、scope=company_all_shares、share_classes_complete=true、coverage_through=研究截点；annual恰为三个完整年度，每行year、implemented_cash_yuan、coverage_complete=true、disclosed_on，正金额须有latest_paid_on且在该财政年度开始后/截点前。记录为按财政年度归属、已实施、全股类汇总并换成人民币的实际现金；0必须是完整覆盖核验的没有实施分红，未知不填0；负值无效。连续分红只验证每年实际额>0，不强制逐年增长，不将该组作为已核实支付率。支付率仍待配置。
- style_liquidity：currency=CNY、scope=a_share_raw_turnover；daily恰为market_window的60个唯一市场交易日{date,amount_yuan}。原始成交金额（元）、非负有限值、缺失不补0，不以个股最近60条替换市场窗口。

### 规则及结果

1. 普通红利价值：三年OCF合计/同期间归母利润合计>=0.8为已确认硬条件；等于通过，低于失败。另现金专项review clear才完成已确认现金核验。金额或范围未知则pending，不允许clear覆盖硬数值。金融不计算该比率，采用类型匹配的现金专项review；未审核不通过。
2. 成长：显示同一现金比率但不以0.8作资格硬线，低于为attention，保留研究资格。现金专项未clear为attention/pending，不因该提示替代共同风险核验。不新增未确认的应收、存货或债务数值。
3. 红利分支：三年均有已实施分红为已确认条件，零分红则该分支失败；缺证据待核验。不强制金额逐年增长。价值分支不要求连续分红；任何红利分支失败不自动导致整个红利价值池失败。两分支仍因支付率/估值等未配置保持pending_configuration，已确认现金失败可令两分支failed。
4. 流动性日均>=1亿元为已确认参考。红利价值低于仅warning不取消资格；成长低于机会observe，高于只代表该单项passed，整体机会仍unavailable（其他阈值未确认）；缺失为unavailable。不因流动性改变共同准入。
5. 每池返回checks（code/outcome/reason/evidence_ids/values/role），approved_conditions_status、style_status、admission_status、opportunity_status、unconfigured_rules；分支有独立结果。common硬失败作用两池；否则未配置最终仍pending_verification。formal_screening_enabled=false、members_published=0。没有任何ready/publish路径。
6. 计算中revision变动/单公司失败时所有嵌套池资格、style与机会结果均失效，不只修改顶层共同状态。不能返回混合时点有效结果。保留来源IDs用于排查。

### 实施及验证

先写引擎边界测试：0.8等于/上下、现金与利润符号区分、分母与共同事实绑定、普通/金融分支、现金review不覆盖失败、成长低比率只提醒、三个年度分红不递增通过、零与未知/未实施/未来/重复、红利失败但价值保留、60日完整/缺失/重复/偏早、1亿元精确边界、来源审核/冲突/过期、全已确认单项通过但正式仍关闭。

实现代理只改style.py及test_stock_style.py；主会话改共享组接口、CLI及集成测试、检查映射、使用文档。S级双审查，修复后定向复审。CLI合成真实SQLite验证现金硬失败、两池分歧与计算期间失效；685真实库仍应全部待核验且无入池。最终相关范围verify及git diff --cached --check后整体本地提交，不推送、不重复全量回归和UI验收。

<a id="historical-2026-10-15-stock-radar-approved-styles-validation"></a>

### 两池已确认风格条件验收记录

> 2026-11-24 文档整理：本节合并自 `2026-10-15-stock-radar-approved-styles-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期：2026-10-15。基于共同准入提交10ad057，复用现有独立工作树和共享venv，无新增依赖、无UI/会话改动。

#### 实际交付

新增 `radar.stocks.style.evaluate_stock_styles` 和 `stock-robot radar stock-style-evaluate`。来源校验复用共同引擎；规范事实分组支持指定前缀，原调用默认行为不变。

- 普通红利价值：三年经营现金流/同期间共同事实归母利润>=0.8，另现金专项核验；金融采用匹配类型的专项事实。
- 成长：比率低于0.8仅attention，不直接淘汰；专项未完成继续待核验。
- 三个年度实际实施分红仅约束红利分支；价值分支独立。金额无须逐年增加，零与未知区分。
- 60市场交易日日均原始成交额1亿元只作红利提醒/成长机会参考，不改变共同资格。
- 资料revision变化时共同结果、两个池、各分支及checks全部失效；单公司失败返回两池显式待核验。原共同准入命令不新增池报告，保持原用途。

正式筛选保持false，成员发布始终0。未确认支付率、股息率、估值、成长与趋势规则保持待配置；已确认条件passed不等于完整风格合格。

#### 检查与审查

先写CLI集成用例并确认5失败1通过（新命令及include_styles尚未实现），再接服务。最后执行：

```powershell
.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv
```

Ruff通过、Pyright 0错误、PowerShell语法检查通过，pytest **297项通过（43.82秒）**。映射无未覆盖文件，范围含53项纯风格、6项风格SQLite/CLI测试，原共同准入80项、共同CLI16项、研究存储、检查脚本及CLI相关测试。首次改造后原96项共同引擎/CLI测试也通过；同状态无需重复全量。

规格及质量审查均PASS。质量审查额外执行7200次各层坏类型、非有限数、极指数变异，无未捕获异常。测试覆盖精确0.8/1亿元上下、现金流与利润符号区分、来源冲突/过期/未审核、金融分支、分红下降与零/缺失、不同财政年度、缺失及重复市场日、人工clear不覆盖数值失败，以及所有嵌套结果的修订竞争失效。

#### 实际研究库验收

通过共享venv正式CLI运行 `radar stock-style-evaluate --db tmp/stock-research-acceptance/final.db --as-of 2026-10-15 --output tmp/stock-research-acceptance/approved-styles.json`，PYTHONPATH明确指向当前工作树。

结果：685家公司、两个池各685项pending_verification、errors空、eligible_members0、members_published0、formal_screening_enabled=false。数据库前后均685公司、772证据、0审核、3批次，没有写入审核或成员。该结果源于尚无已审核的规范事实，不代表市场达标公司数为0。

研究报告/数据库/失败诊断留忽略目录tmp，不提交。当前仍未完成完整风格/机会规则、不可变评估存储、成员发布及生命周期、后台任务及完整管理页面；无本阶段UI变更故不重复前阶段浏览器验收。真实全市场风格通过数量、完整采集耗时及内存峰值未验证。本地提交不含推送、合并或发布。


<a id="source-2026-10-15-stock-radar-capital-history"></a>

## 2026-10-15 第一阶段：官方股本变动原文与双日期研究记录

> 来源：`docs/superpowers/plans/2026-10/2026-10-15-stock-radar-capital-history.md`；整理前版本 `ad626dc`。


### 授权与范围

用户要求持续完成完整股票池且不再逐步确认；继续已批准数据接通，沿用design-before-implementation。第一阶段子事项，不新增交付阶段。公开原文可采集，不新增投资阈值、不推断单位、不发布成员。S级跨API实现及独立规格/质量双审。

### 已验证事实

2026-10-15公开POST https://webapi.cninfo.com.cn/api/stock/p_stock2215，params scode/sdate/edate；两个完整15秒deadline探针600900返回69条、601318返回52条。raw及metadata仅tmp/stock-capital-source-probe。SDK stock_share_change_cninfo字段映射：DECLAREDATE公告日，VARYDATE变动日，F003N总股本，F021N已流通股份，F022N为其中人民币普通股；F028N流通受限，另有受限B/H股。F022N不能代表全部A股。2007-03-01平安流通A/H之和不足总股本；长电有GDR上市，不能推断另加GDR或底层股数。SDK和响应均无已核实单位，保持unit=None/unit_verified=false/all_share_classes_verified=false。

SDK公开动态头仅安装包cninfo.js本地getResCode1纯时间编码，无账户/验证码/Cookie。仅执行可信安装常量，V8 eval/call各2秒、64MiB，生产完整POST含头生成/响应读取由bounded_provider_call总15秒。遇401/403/429或权限/验证码挑战停止来源，不换身份绕过，不留动态头。

### 模块契约

新增radar.stocks.capital_history，capital_url(symbol)固定官方HTTPS URL；parse_capital_history(raw,symbol,*,as_of,start_date)->dict。严格原始JSON（拒非有限数、重复键），校验resultcode200、records列表、count/total与实际记录数量；无法证明完整时complete=false。保留全部source_fields，不改写数值，不乘10000。合法六位symbol须逐行完全匹配，两个日期严格合法；只在公告和变动均在截点内且变动>=start_date时进入rows，其余excluded_rows有原因。重复相同双日期记录全部排除且不认完整，冲突不择优。同一变动日不同公告的修订保留并提示，不选latest数值、不合成股数序列。原文错误包/挑战拒绝。rows只给announced_on/effective_on/source_fields，unit=None及上述false标志，capitalization_fact绝不生成。完整仅指请求范围原文完整解析，不保证全历史无漏报或所有股类。

collect_capital_history(companies,cache_dir,*,as_of,start_date=date(1990,1,1),refresh=False,http_post=None,sleep=...)->dict。预校验全部symbol/重复/date，不晚于北京时间当前日。当前/历史请求都保留双日期过滤；历史导出声明retrospective=true、point_in_time_verified=false，不冒充当时原文快照。默认历史起点1990-01-01只是请求范围，不是已验证上市日期。原文4MiB、行数20000、串行1秒、timeout15、网络/5xx最多重试2次（沿用功能设计第6节，重试退避3秒、10秒）、429/401/403/挑战停止后续同源网络；缓存可用不受停止影响。非瞬态格式/权限不重试。每公司失败隔离。

复用已安装依赖，不引入新包。预算内HTTP200原文按sha256 .jsonbody先归档；manifest版本/symbol/start/asof/URL键，命中验实际限长/摘要/aware fetched_at不未来、重跑parser，不修改采集时间；refresh失败不得回旧成功。原子写、拒缓存root/祖先/子文件符号链接；1GiB只提醒不删除，统计失败不影响采集。保存失败清单和raw hash；不自动写研究DB或生成可确认市值事实。

CLI stock-capital-history --input companies.json --as-of --start-date(默认1990-01-01) --cache-dir --output [--refresh]；输入和输出不能互相覆盖/位于cache内，未来/倒序日期在任何采集写入前拒绝。导出input_source摘要，research_only=true/financial_verified=false/formal_screening_enabled=false/members_published=0。

### 分工与验证

实现代理仅capital_history.py和test_stock_capital_history.py；父CLI/CLI测试/注册/verify映射/文档/真实验收。资源tests/resources/radar/cninfo-600900-capital-history.json与cninfo-601318-capital-history.json，真实响应各裁3条（最新、最早或上市及GDR/受限），保留真实total/count，默认不能声称全量完整；正向测试明确合成裁切总数。采样2026-10-15，原始数字JSON精度按原文保留，完整响应只tmp。

测试先行覆盖双日期未来/公告晚于变动、单位未知/受限股/GDR不推算、错误包/身份/非有限/重复键/重复冲突/截断数量；HTTP总deadline/close/重试停止/预算、缓存hash/时间/符号链接/失败刷新、历史非时点证明；CLI路径/输入先拒、无DB改写、注册。真实两公司下载及缓存复用，不自动审核/入池。相关verify门禁通过才完整事项本地提交，不全量。

### 实施记录

首轮CLI红态因新域模块尚未实现，ModuleNotFoundError radar.stocks.capital_history；测试产物保留。静态检查同样记录缺模块及导入排序，后续模块落地后再复核。

原始600900响应SHA256：d8283af04af279507fcc4dee629cb739606a890ed9a52515a090d633d09f0fe7；字节数49834。

原始601318响应SHA256：47f9ae9871ff819360baa2bcab2db9cb61b7df407529432259eab5c72830bb20；字节数38063。

数值保真：source_fields保留源JSON数字类型，另附source_numeric_tokens保存源数字token字符串，完整原文bytes按SHA归档；不依浮点转写承担精度审核。规格预审确认count/total如同时存在应分别等过滤前原始records数量。公开文档目录已确认p_stock2215与增量接口，但目录无单位或更正语义，未核实状态不变。

收尾修复：顶层status原先仅按下载/缓存成功判断，现同时要求每家公司解析complete=true；count/total不一致即使HTTP200也只能是partial。已增加回归。真实CLI采集600900 69条、601318 52条，第二次均命中缓存且摘要/采集时间稳定；单位和全部股类覆盖仍为false。最终暂存范围门禁：`.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`，Ruff通过、Pyright 0错误、pytest 189 passed（62.90秒），测试临时目录已清理。


<a id="source-2026-10-15-stock-radar-common-admission"></a>

## 2026-10-15 已确认共同准入条件计算实施计划

> 来源：`docs/superpowers/plans/2026-10/2026-10-15-stock-radar-common-admission.md`；整理前版本 `ad626dc`。


日期2026-10-15。承接完整股票池已确认设计及持续推进授权，紧接持久化提交798c21e。只实现已确认共同条件的确定性计算，不替用户批准未确认风格／机会阈值，不发布成员。

### 一、输入与来源约束

新增radar.stocks.admission纯计算模块和stock_robot.stock_admission CLI服务。输入来自StockResearchStore的当前证据及逐条审核，研究提示/输入自带verified不具效力。只消费明确kind的规范事实组：admission_security、admission_capitalization、admission_profitability、admission_audit、admission_governance、admission_leadership、admission_financial_quality。其他kind忽略，不把解析建议自动拼成已核实事实。

每组证据须review_status=confirmed、latest_review为confirmed且reviewed_at不晚于评估日结束、距评估日不足90天；source disclosed_on不晚于评估日，HTTPS+hash完整，symbol一致，confirmable=true。服务只从数据库读这些状态，CLI不能传入任意passed结论。无有效组为待核验，失效/驳回不能当零。多个有效不同payload同组冲突为待核验，不擅自挑一个；相同payload多来源可合并。

CLI `stock-admission-evaluate --db PATH --as-of YYYY-MM-DD [--symbol CODE] [--market-window JSON] [--output PATH]`。无symbol评估全共享候选。market-window为显式研究用市场日期列表（60个已核验市场交易日，严格递增且最后日不晚于截点），不推断工作日；未提供则市值窗口待核验。该文件/研究上下文不是正式发布授权，后续生产任务需绑定官方交易日历版本，不能以任意JSON作为发布时行情时点。输入／输出保护DB及日志，输出JSON不可覆盖market-window。

服务读取每公司revision前后校验，资料变化则该公司返回待核验并提示重试，不保存错误的稳定结论。此阶段输出独立研究报告，保留source_revision、as_of、evidence_ids与rule_version，不覆盖StockResearchStore.admission_status或成员；风格未配置时，即使共同条件passed，最终admission_status仍pending_verification，formal_screening_enabled=false。共同硬条件failed可在研究报告表示excluded，不能贴无依据造假标签。

### 二、规范事实及计算

所有金额为人民币元，可接受有限数值字符串，bool/非有限值/空值/不合理数量级为未知，不填0。只用Decimal，求和精度复用已验证支付率模块工具。规范组payload即该组数值与判断，来自显式事实审核，不允许用verdict覆盖下述硬数值。

1. admission_security：exchange=SSE/SZSE、security_type=ordinary_a、a_listed_on=YYYY-MM-DD、issuer_type=ordinary/bank/securities/insurance；未知型待核验，明确其他证券或市场排除。上市按完整3周年，2月29日目标非闰年采用2月末；未来上市日期为无效资料，不贴失败经营标签。
2. admission_capitalization：currency=CNY、scope=company_all_shares、share_classes_complete=true；current={date,amount_yuan}，daily=[{date,amount_yuan}]。current日期必须等于所供市场窗口末日，daily必须逐日唯一完整匹配60个窗口日期；负市值无效。当前和60日平均均>=20000000000才pass，任何有效已核实项低于则fail，即使另一项缺失也保留确定性失败；缺窗口不以更早60条代替。
3. admission_profitability：currency=CNY、scope=company_parent，annual=[{year,parent_profit_yuan,deducted_parent_profit_yuan,disclosed_on}]覆盖as_of.year-3..as_of.year-1唯一完整年度；ttm={period_end,disclosed_on,parent_profit_yuan,deducted_parent_profit_yuan,latest_announced:true}。已披露的每项均>0，0或负值fail；未知/重复冲突/未来披露不能放行。TTM必须提供独立明确记录，不能把年度利润当TTM；latest_announced需已审核，period_end<=disclosed_on<=as_of，年度也须在结束后披露。三完整年度与最新TTM盈利均为独立条件。
4. admission_audit：verdict=clear/failed/pending及非空basis，years恰为三个完整年度；非空basis说明意见与整改核验，缺期间不能通过。该组是明确的事实专项核验，不用标准意见替代治理。
5. admission_governance：verdict=clear/failed/pending、basis非空。failed仅表述治理核验未通过，不自动对外标监管造假。来源真实性仍须证据审核。
6. admission_leadership：verdict=clear/failed/pending，basis、industry_level1、industry_level2、business_scope非空。不按市值排名自动给龙头标签，不设未经确认的排名阈值。
7. admission_financial_quality：verdict=clear/failed/pending、basis、issuer_type必须与security一致，金融不可由普通企业事实替代。银行/证券/保险专项内容保留在basis及payload，不擅加监管数字。

结果checks为稳定code/outcome(passed/failed/pending_verification)/reason/evidence_ids/可选values。common_status任一有效硬fail为failed，无fail但未知为pending，全部条件pass才passed；audit/governance/leader/quality专项failed亦fail。common条件不包含1亿元流动性淘汰线，也不把成长池现金流0.8作为共同硬线；两池现金／风格／机会继续独立，正式筛选关闭。这一阶段不把普通股利润口径要求错误加到共同持续盈利，支付率普通股口径仍独立。

### 三、实现与验收

纯引擎由实现代理新增src/radar/stocks/admission.py、tests/radar/test_stock_admission.py，主会话写CLI/服务、集成测试与映射。接口 `evaluate_common_admission(symbol:str, evidence:list[dict[str,Any]], as_of:date, *, market_window:list[date]|None=None)->dict[str,Any]`。组从证据kind精确匹配，values与来源IDs入结果。evidence列表结构按StockResearchStore.list_evidence：symbol/kind/payload/source_url/content_sha256/disclosed_on/confirmable/latest_review/review_status；内部真实hash字段名以当前存储为准，切勿猜测。rule_version固定 `common-approved-2026-10-15-v1`，独立formal_screening_enabled=false，admission_status排除或待核验。

测试先行：200亿精确边界、60窗口缺失重复/更早日期/金额缺失、不用float，上市周年闰年，三个年度和TTM0/负值/缺失/非有限/未来/重复/币种口径，证明人工clear不能覆盖数字fail，过期/未来审核/证据归属冲突，定性组三年及金融分支、全pass仍不能直接入池、同组三份冲突不选优、未知无0。主会话CLI真实研究库685家公司无审核应全部待核验；合成完整核验数据验证共同pass、边界fail但不发布。双审查及相关门禁后独立本地提交，继续后续风格/发布/管理服务，不把本阶段称完整系统。

<a id="historical-2026-10-15-stock-radar-common-admission-validation"></a>

### 共同准入计算验收记录

> 2026-11-24 文档整理：本节合并自 `2026-10-15-stock-radar-common-admission-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期：2026-10-15。工作树：`codex/stock-radar-pools`，承接持久化提交798c21e。

#### 交付范围

新增纯计算 `radar.stocks.admission.evaluate_common_admission`，只消费七类有效且显式审核的规范事实，逐项计算上市满三周年、当前及60市场交易日均值200亿元、三个完整年度及最新TTM归母与扣非归母利润为正，以及审计、治理、龙头、对应企业类型的财务专项。

新增 `stock-robot radar stock-admission-evaluate` 及SQLite服务：独立研究报告保留事实来源、规则版本和公司修订号；公司资料计算中变化则失效，单公司读取/计算失败隔离。未确认风格及机会参数仍待配置，正式筛选关闭，不写入成员或覆盖公司资格。

代码、命令、检查映射及使用说明按同一事项收敛提交。没有UI或会话改动，本阶段不重复既有真实浏览器/会话验收。

#### 测试与审查

执行 `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff通过、Pyright 0错误、PowerShell语法检查通过、pytest **237项通过（54.36秒）**，临时目录由验证脚本成功清理。检查计划无未映射文件，覆盖：

- tests/test_verify_script.py
- tests/radar/test_stock_admission.py（80项纯引擎断言）
- tests/radar/test_stock_admission_cli.py（16项真实SQLite/CLI集成）
- tests/radar/test_stock_research_store.py
- tests/test_cli.py、tests/test_cli_index.py、tests/radar/test_collector_cli.py

规格及质量双审查最终PASS。审查修复覆盖302代码、同日全股类市值相互矛盾、极端数值指数、单公司首次读取隔离、失败结果的日期/版本/证据字段。已审核事实内的clear不会覆盖数字失败；已知独立失败不因其他缺失项消失；矛盾事实本身不能选择有利值通过。质量审查还做3864次坏类型动态探针，没有未捕获异常。

#### 685家公司真实研究库验收

使用现有 `tmp/stock-research-acceptance/final.db`，通过共享venv正式CLI入口执行：

```powershell
$env:PYTHONPATH="$PWD/src;$PWD"
D:/code/stock_robot/.venv/Scripts/stock-robot.exe radar stock-admission-evaluate --db tmp/stock-research-acceptance/final.db --as-of 2026-10-15 --output tmp/stock-research-acceptance/common-admission.json
```

前后数据库统计相同：685家公司、772条证据、0条审核、3个导入批次。研究报告：total685、common_passed0、common_failed0、pending_verification685、errors空、eligible_members0、members_published0、formal_screening_enabled=false。公司尚未完成规范事实审核，所以不能把候选提示当合格成员；此结果不是“市场没有公司达标”的统计。

报告及数据库留在忽略的tmp目录，不提交原始资料。实际筛选通过比例、完整批量采集耗时、内存峰值、正式风格/机会引擎、发布及成员生命周期、完整管理UI均未由本阶段验证。研究用日期文件不替代未来发布所需的生产交易日历和行情版本；当前审核状态不构成历史审核回测。


<a id="source-2026-10-15-stock-radar-evaluation-archive"></a>

## 2026-10-15 评估留档与版本化成员草案实施计划

> 来源：`docs/superpowers/plans/2026-10/2026-10-15-stock-radar-evaluation-archive.md`；整理前版本 `ad626dc`。


日期2026-10-15。基于d37eb24，执行已确认完整设计及本轮继续授权；沿用design-before-implementation技能，不重复询问已确认流程。此次完成不可变研究评估档案及版本化成员新增草案的底层/CLI，不声称成员发布、风险生命周期、后台任务或UI已经完成。未配置规则继续阻止正式入池，不新增依赖、不改ETF和全局SQLite user_version。

### 一、不可变研究档案

新增 `radar.stocks.evaluation_store.StockEvaluationStore(research:StockResearchStore)`，同SQLite独立stocks前缀表及独立feature版本1，不改变research现有schema_versions=1。复用research连接/写事务/时钟，连接必须closing。

接口：
- `capture_inputs(*,symbol:str|None=None)->dict`：保存scope all/single、按symbol排序的companies列表（每项company+evidence），公司完整资料、证据payload/来源/hash/披露及最新审核身份/决定/时间均留档；不只是保存证据ID。before/after修订保证来源稳定，不隐含通过。captured_at仅作记录，排除幂等身份。
- `save_evaluation(report:dict, inputs:dict, *, market_window:list[date]|None=None)->dict`：报告由服务器现有evaluate_store(include_styles=True)生成，CLI不接受导入passed JSON。保存前BEGIN IMMEDIATE下校验公司revision与报告source_revision；scope=all还校验完整当前证券名录，变化抛StockRevisionConflict且整批零写入。校验规范日期、唯一代码、来源JSON、当前共同/风格规则版本，formal_screening_enabled必须false、members_published=0、research_only=true，池/分支不能有eligible/ready资格。执行errors或evaluation_valid=false可留partial诊断报告，但不成为complete；partial不覆盖任何旧complete。
- 身份为规范化report+输入manifest（去captured_at）+市场window的SHA256，同输入重复保存幂等，不把created_at计入ID；已存在快照不更新原文或时间。
- `get(evaluation_id)->dict|None`：id/status/created_at/as_of/report/inputs/context及动态freshness，返回副本；freshness校验来源revision、all范围名录、规则版本和本地当前日与研究截点，旧研究日不冒充当前资格。归档内容不因新审核覆盖。
- `list_evaluations(*,symbol=None,page=1,page_size=15)->dict`：SQL分页稳定created_at DESC/id，symbol按快照实际包含过滤，正整数参数保护。返回摘要，不每页完整大payload。
- `assert_current(evaluation_id)->dict`：后续草案使用；完整、来源版本一致、规则一致才可引用；历史研究可看但不能当今日草案依据。不可用/缺失/部分失败明确错误或StockRevisionConflict。

来源manifest是服务内部数据而非外部批准输入。报告与manifest归属/版本相互校验，不能用存储接口批准正式成员；未来生产发布仍需完整规则与正式行情/日历版本，研究快照不能单独批准成员。review90天自然失效与公司资料变化均影响动态freshness；研究输入的未来审核、披露仍由引擎校验。

### 二、成员新增草案（发布仍关闭）

新增 `radar.stocks.membership_drafts.StockMembershipDraftStore(evaluations:StockEvaluationStore,pools:StockPoolRepository)`，独立stocks草案表与追加事件表。此次没有正式成员表写路径，因此只接受action=add；restore/remove/suspend不能伪造已有成员，后续生命周期模块再提供。

接口create(*,evaluation_id,changes:list[{pool_id,symbol,action:'add',reason}],request_id,actor)->dict、get(draft_id)->dict|None、list_drafts(page=1,page_size=15)->dict、cancel(draft_id,*,expected_revision,actor,reason)->dict。请求id非空且不超过200字；同id同规范内容幂等，异内容冲突。变化不得重复pool/symbol，至少一项，池须配置存在/启用；仅支持实际计算器产出该池结果的已配置股票池，新风格不能因命名自动生效。原子检查evaluation当前完整版本、公司属于该评估，再写入草案和created事件，任一项无效整批回滚。

草案revision从1开始、状态blocked；逐项保留公司/池、理由、评估id、来源revision、未通过/待配置阻碍。没有ready/eligible绕过参数。cancel使用revision CAS，追加取消人、理由、时间及事件，重复旧revision报冲突。get/list保留原草案并附动态evaluation_current，来源变化不改写旧判断，也不能发布。无publish入口，返回publication_enabled=false、members_published=0。真实两池成员保持空。

### 三、CLI/流程

新增 `stock_robot.stock_archive`：
- stock-evaluation-capture --db --as-of [--symbol] [--market-window] [--output]：先capture_inputs，再服务器evaluate_store，保存档案；输出档案摘要/ID。复用原市场窗口读入/文件保护辅助函数，所有输入输出禁止覆盖DB/WAL/SHM/journal，也不能覆盖市场window。
- stock-evaluation-list --db [--symbol --page --evaluation-id --output]。
- stock-member-draft-create --db --input --pool-dir --request-id --actor [--output]：JSON仅evaluation_id及changes，不接受passed/eligible/正式成员JSON。pool-dir缺省config/radar_stock_pools。
- stock-member-draft-list --db [--draft-id --page --pool-dir --output]。
- stock-member-draft-cancel --db --draft-id --expected-revision --actor --reason [--pool-dir]。

CLI全部是实际SQLite往返，不改网页或会员读取API。本阶段结束仍不具备完整成员发布/暂停/退出/恢复及股票更新功能。

### 四、实施与验收

存储代理实现evaluation_store.py+test_stock_evaluation_store.py；父实现草案+CLI/集成测试/映射/使用说明。测试先行、相关范围检查、规格+质量独立审查；审查修复后定向复核。重点不可变/幂等/来源原文存档、审核变更、创建/保存间race、all名录新增、部分失败不覆盖、日期/自然过期、cross-company、请求重放、draft取消CAS/原子失败、文件保护、旧接口兼容。

使用现有685家研究库做真实CLI留档两次应复用同档案；有完整运行但无已审核事实时档案complete仅代表计算完成，资格仍pending。草案合成真实库验证可保存blocked但不能发布，再更新证据验证动态失效。资料/截图/DB留tmp不提交。verify Changed Staged覆盖新模块与原共享存储/引擎/CLI相关范围，git diff --cached --check后整体本地提交；不隐式全量、不推送。测试目录唯一PID+GUID，失败保留、全部结束后尝试清理成功目录。

<a id="historical-2026-10-15-stock-radar-evaluation-archive-validation"></a>

### 评估留档与成员草案验收记录

> 2026-11-24 文档整理：本节合并自 `2026-10-15-stock-radar-evaluation-archive-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期2026-10-15。承接d37eb24，工作树codex/stock-radar-pools。本阶段是已确认流程的底层/CLI实现，无网页或会话改动，无新增依赖。

#### 实际交付

- StockEvaluationStore：同SQLite独立feature版本和表；保存完整公司/证据原文/审核身份及时间、计算报告、规则版本和市场上下文。规范内容SHA256幂等，captured_at不进入身份，旧档案不改写。保存事务内核对revision、完整来源、全范围名录；部分失败独立留档。动态检查日期、规则、来源和90天审核失效。
- StockMembershipDraftStore：新增关注草案、请求幂等、整批原子校验、blocked原因、版本化撤销及追加事件。仅支持add，无正式成员写入或发布入口；来源变化后旧草案保留并动态失效。
- 五个CLI：stock-evaluation-capture/list、stock-member-draft-create/list/cancel。capture仅服务端重新计算；外部JSON不能批准资格。保护DB/WAL/SHM/journal、市场窗口、草案输入及正在读取的股票池配置目录。
- 原研究命令共享市场窗口解析，行为保持原用途；检查脚本增加明确映射，使用说明列明complete/freshness均不等于资格。

#### 验证及审查

测试先行：草案模块不存在时收集失败；新CLI尚未注册时3失败5通过；实现后补充并发重放、部分失败依据拒绝、未知schema版本、错误路径断言及池配置保护测试。

执行 `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`，Ruff通过、Pyright 0错误、PowerShell语法检查通过，pytest **356项通过（50.85秒）**；验证脚本成功清理本次目录。改动无未映射文件，覆盖：

- 评估档案25项、成员草案21项、归档CLI10项。
- 原共同引擎80项、共同CLI16项、风格引擎53项及风格CLI6项。
- 共享研究存储、verify映射及CLI相关测试。

规格与质量审查最终均PASS，覆盖核心、CLI及使用说明。质量审查发现草案导出可覆盖池配置YAML，已修复为resolve后拒绝输出进入pool-dir，create/list两项回归验证原YAML不变，定向复审通过。没有遗留审查问题。未运行全量回归，未重复无本阶段改动的真实浏览器/会话验收。

#### 真实685库留档

使用共享venv正式stock-robot.exe入口及当前工作树PYTHONPATH，对 `tmp/stock-research-acceptance/final.db` 两次执行stock-evaluation-capture，截点2026-10-15。输出archive-capture-first.json与archive-capture-second.json。

两次ID均 `877f567be450421105372c21bee84acefaae2e45b4a72c6670742e5a8483a4a0`，首次created_at均2026-10-15T05:54:51.355044+00:00。仅1份档案，status=complete、freshness.current=true、companies_total=685。原数据库统计仍685公司、772证据、0审核、3批次。formal_screening_enabled=false、members_published=0；所有候选资格仍待核验。complete仅表示计算完整，不能用于声称通过选股或正式入池。

#### 正式命令合成草案验收

另用 `tmp/stock-research-acceptance/draft-executable-probe/synthetic.db`，公司名称及署名明确为合成测试，不是用户首批关注选择。通过正式exe创建档案、保存草案、更新证据再查询草案：初始status=blocked、revision=1、evaluation_current=true；资料更新后evaluation_current=false，原因source_revision_changed；档案及草案保留，publication_enabled=false、members_published=0。撤销及旧revision拒绝由真实SQLite集成测试覆盖。

真实库/合成库/输出报告与失败诊断保留忽略目录tmp，不提交。正式成员发布、暂停/退出/恢复、风险生命周期、后台采集任务及管理UI仍未完成；研究档案不替代生产行情/交易日历版本，也不重建历史时点审核。尚未验证全市场正式筛选通过数量、完整采集耗时与内存峰值。本地提交不隐含推送、合并或发布。


<a id="source-2026-10-15-stock-radar-financial-facts"></a>

## 2026-10-15 第一阶段：年报建议到规范财务事实的接通计划

> 来源：`docs/superpowers/plans/2026-10/2026-10-15-stock-radar-financial-facts.md`；整理前版本 `ad626dc`。


日期2026-10-15，基于8f04ebc，属于已确认四阶段中的第一阶段数据/规则补齐，非新增交付阶段。继续执行design-before-implementation技能和用户无需逐小项确认的授权。本次完成保留来源的三年财务规范组准备/待审保存，不增加投资门槛、不批准来源、不把年报当TTM。

### 输入与输出

新增radar.stocks.financial_facts.prepare_financial_facts(symbol,evidence,as_of)->dict。输入为StockResearchStore.list_evidence真实结构。只消费本公司financial_proposal：confirmable=true、公开HTTPS/domain/SHA64、披露<=截点、fields与origin公司/URL/披露/file_sha一致、origin/fields.status=matched_template，fiscal_year为最近3完整年度、CNY，parent/deducted scope=reported_parent，OCF/revenue scope=consolidated，金额admission._amount校验且不丢Decimal精度；年度披露须在年度结束后。明确rejected/needs_evidence/expired或审核未来/无效者不采用；无审核建议可以整理，但不能因此自动确认。保留负值/零，不把未知填0。

按年度/metric合并数值相同来源，Decimal等值不同格式为相同；不同数值冲突则该值None并列全部来源，不选最新/有利的一份，也不自动解释重述。每项保留evidence_ids、PDF URL/hash、页码、excerpt、原始单位和审核身份；最新年度与比较年度分别归属，不依赖proposal_id而取数据库evidence_id。财务field声明不替代用户核对范围。

结果含version/as_of/symbol/facts/annual_summary/unresolved/requires_review=true/financial_verified=false/formal_screening_enabled=false/members_published=0。facts仅在至少有一项相应年度有效值/冲突时产生：
- admission_profitability payload currency=CNY、scope=company_parent、三年annual{year,parent_profit_yuan,deducted_parent_profit_yuan,disclosed_on}、ttm=null；TTM明确未提供，不以年报代替。reported_parent到company_parent是待审规范映射，不构成机器确认。
- style_cash_flow payload issuer_type仅从有效已审核admission_security取得（否则null）、currency=CNY、scope=company_consolidated、parent_profit_scope_reconciled=false、三年annual{year,operating_cash_flow_yuan,disclosed_on}。金融已知类型不伪装ordinary；cash ratio不会因简单确认而通过口径条件。

fact外层kind/payload/source_url/disclosed_on/content_sha256。顶层URL/hash确实对应依赖中披露最新的一份原PDF，不拿合成JSON hash冒充PDF；各字段真实原文在payload来源映射中完整保存。若无合法原文则不产生fact。

### 来源依赖与存储

每个payload保留保留字段 `_financial_dependencies`={version:1,as_of:研究截点,inventory:[{evidence_id,review_id}]}，inventory覆盖该公司全部financial_proposal（包括未用/驳回/未知），按ID排序。新增同类建议或任一审核变化即失效，不让旧组绕过后来的修订/驳回。现金事实另保存使用的证券来源快照（若有）；验证不允许跨公司ID。依赖来源字段仅使用数据库真实ID/latest_review.review_id，无review记null。

StockResearchStore新增add_financial_fact_batch(symbol,facts,*,expected_revision)，在同一BEGIN IMMEDIATE事务中验证kind只限上述两种、每种唯一、严格保留依赖、inventory与本公司数据库当前集合/审核ID一致，source URL/hash须属于列出的合法原文；组资料由服务端准备，CLI不导入外部passed JSON。原文字段及来源映射仍在payload，保存调用现有_evidence/insert方法。所有新增组为待核验，绝不添加review。整个batch要么成功要么失败；新增时公司revision一次递增，同内容再次保存复用不重复新证据（仍要求当前expected_revision）。

现有add_evidence遇到该保留依赖字段也要进行相同验证，不能通过通用入口绕过。save_review在confirmed前核对依赖仍有效，失效不能确认。list_evidence对已经审核的派生组动态检查依赖；若inventory/审核变化，review_status=needs_evidence并附lineage_status=stale，保留旧审核历史但不给引擎使用；未审核的组也标明失效。不因非财务提示新增而无条件失效，不改变普通手工证据无依赖时的现有行为。字段名保留给服务器来源绑定，不作为用户verified权限。

### CLI

新增stock_robot.stock_financial_facts，命令stock-financial-facts-prepare --db --symbol --as-of [--save --expected-revision N] [--output]。默认只读准备；save必须显式带expected-revision，由同一次服务端读取/准备产生fact批次，再原子保存待审。输出公司revision、事实组/未解决事项及可审核证据ID。DB家族文件保护复用既有帮助，不覆盖源文件；此入口不接任意fact JSON、不发网络请求。

### 验证与审查

实现代理只写financial_facts.py及test_stock_financial_facts.py，父写research_store依赖校验、批次和CLI/集成测试、verify映射、使用说明。测试先行，S级规格+质量双审查与定向复审。覆盖来源/身份/日期/币种/口径/坏类型/负零/Decimal/比较年度、重复等值合并/冲突None、未审核仍待审、TTM不回填、cash口径false、跨公司与原文伪造拒绝、依赖新review/新增建议失效、原子CAS、旧手工证据兼容。

真实库685家公司仍0审核，不代用户确认实际财务；对已有长江电力年报资料先只读准备并报告覆盖/冲突。若需保存验收，用独立合成库验证待审核保存、显式确认后仅年度检查可用而TTM/现金口径仍待核验、上游审核变化失效。相关范围verify Changed Staged及diff --cached --check通过后本地提交，研究产物留tmp，不推送、无UI修改不重复浏览器验收。此项完成仍不代表第一阶段全部完成：最新季度/TTM、行情/行业等接通与完整风格阈值尚待后续。

补充审查收敛：保留来源依赖的事实须按dependencies.as_of从当前数据库原始建议和证券来源重新生成，并精确核对完整规范payload及主原文元数据；仅核对field_sources不足以防金额/TTM篡改。无依赖的既有手工事实保持原行为，派生组不允许沿用年报依赖补造TTM或自动勾选现金口径。

<a id="historical-2026-10-15-stock-radar-financial-facts-validation"></a>

### 第一阶段年报财务事实接通验收记录

> 2026-11-24 文档整理：本节合并自 `2026-10-15-stock-radar-financial-facts-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期2026-10-15，基于8f04ebc。四阶段中的第一阶段子项，不是新增交付阶段，也不是第一阶段全部完成。复用独立工作树/共享venv，无新增依赖、无网络采集、无UI或会话改动。

#### 交付及不变量

新增financial_facts纯准备器：读取数据库financial_proposal，按三完整年度/指标整理规范利润与现金流组，保留逐项原文/页码/PDF URL/SHA256/原始金额和审核版本。等值合并、冲突None、不选有利版本；TTM始终null，现金parent_profit_scope_reconciled始终false，issuer未知不当普通企业。科学计数在展开超100字符时保留精确表示，仍可被下游_amount解析。

StockResearchStore新增add_financial_fact_batch。保存事务内CAS、整批原子、同内容复用且不增加review；通用add/confirmed review/list均对保留的_financial_dependencies按as_of从当前数据库重建整个事实，精确比较规范金额/年度/披露/TTM/口径/全部来源。上游新增建议或review变化、驳回/自然到期使旧组needs_evidence/stale；不相关research_hint不使该组失效。既有无保留依赖的手工事实沿用独立来源审核，不给派生组借原文改金额的入口。

新CLI stock-financial-facts-prepare默认只读，--save必须显式expected-revision，由服务器同次读取/准备再原子保存待审。不接受外部passed JSON，不自动确认、不发布成员，DB及日志输出保护。

#### 检查与审查

测试先行：存储新行为11项失败复现缺口，新CLI注册前7失败2通过；实现并增加完整准备到保存、派生金额/TTM/披露/现金scope篡改、冲突值、上游失效及科学计数回归。

执行 `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff通过，Pyright 0错误，PowerShell语法检查通过，pytest **324项通过（56.83秒）**，验证脚本成功清理临时目录。无未映射文件，覆盖新准备器48项、来源存储17项、新CLI10项、年报提取、共享研究存储/CLI、评估档案、成员草案、ETF存储及verify/CLI相关范围；不是全量回归。

规格与质量双审查最终PASS。关闭两项实质接通问题：部分年度field_sources=[]可表示未知并正常保存；不再仅检查引用而遗漏规范实值，改为事务内完整重建以拒绝金额/TTM/口径篡改。质量另做348次坏类型动态变异无未捕获异常，并修复1e-100/1e-1000合法源值展开后下游拒绝的问题。使用说明及检查映射已复审，无剩余审查问题。

首次CLI实现命令的自动审批发生一次超时，确认未执行后按允许范围重试成功；没有安全审批阻断或权限扩大。

#### 实际资料只读验收

正式stock-robot.exe入口、PYTHONPATH指向工作树，执行 `radar stock-financial-facts-prepare --db tmp/stock-research-acceptance/final.db --symbol 600900 --as-of 2026-10-15 --output tmp/stock-research-acceptance/financial-facts-cypc.json`。

读取24条上游财务建议，2023/2024/2025各年归母、扣非归母、OCF及收入共12项available，形成admission_profitability/style_cash_flow两待审组，saved=null、requires_review=true。14条未解决/未采用诊断包含非目标年份建议，不能解读成14项经营风险。库仍685公司、772证据、0审核、3批次，无实际数据保存、确认或入池。available仅表示建议可整理，不代表财务已审核。

#### 正式命令合成保存验收

独立tmp/stock-research-acceptance/financial-facts-executable-probe合成库，通过正式exe --save+expected-revision保存2个待审事实组。仅在该合成库显式确认利润组后，annual_parent_profit_2023=passed，ttm_parent_profit=pending_verification，最终admission_status=pending_verification。随后把一条上游原文审核改needs_evidence，派生组立即review_status=needs_evidence、lineage_status=stale，成员发布始终0。合成数值不是实际公司事实或用户首批关注选择。

研究/合成DB、导出及失败诊断保留忽略tmp目录，不提交。最新季度/TTM、行情/行业接通、未确认选股阈值、正式发布/风险生命周期、后台更新及管理UI仍待后续；本轮未完成第一阶段。未验证全市场正式达标数量、完整采集耗时或内存峰值，未重复无改动的浏览器验收。不推送、合并或发布。


<a id="source-2026-10-15-stock-radar-interim-ttm"></a>

## 2026-10-15 第一阶段：季度/半年报、TTM与年度事实接通计划

> 来源：`docs/superpowers/plans/2026-10/2026-10-15-stock-radar-interim-ttm.md`；整理前版本 `ad626dc`。


日期2026-10-15，基于664ace0。用户明确要求持续执行、无需确认；沿用已确认四阶段与design-before-implementation技能，不为子项重复确认。此阶段后仍继续成员管理等，不把技术子项当新交付阶段。未确认的股息/估值/成长/趋势数字仍待配置，正式筛选false。

### 已探针事实和样本

已使用现有collect_documents取得官方PDF（原文/tmp不提交）：
- 巨潮2026-04-30/1225262110.PDF，600900 2026Q1，SHA3536efda5117d4ef071729a16b9de8449846aedca0a8f2d7ff9a5eafa2cafe84，当前/上年同期/增幅3列，金额表第1页。
- 巨潮2026-08-31/1225534923.PDF，2026H1，SHA738de3b36a31646f344cefaf90b4f149d653c3f68e89f4f8d14bdf02fc9c65a2，主要会计数据第6页，当前/上年同期调整后/调整前/增幅4列。比较期间同一控制合并重述，不能自动认定可比。
- 巨潮2025-10-31/1224776945.PDF，2025Q3，SHA1efb73a0c2327419f6c9d571bd34814f8c4672cbc4d3def87c414e65b3f6d3c0，表第2页，同时单季度/年初累计，调整列共8列，OCF前四列不适用。

网站另有同报告不同上传版，不把文件上传日当交易所原披露日；正式样本用上述巨潮路径日期。测试静态资源只裁身份标题及表页主要会计数据：tests/resources/radar/cninfo-600900-2026-q1-accounting.txt、...2026-h1...、...2025-q3...。保留原始分页和pypdf文本断行，完整PDF留tmp/stock-interim-probe/pdf。

### 1. 季度公告及保守解析

公告发现增加mode=interim_report，三个关键词第一季度报告/半年度报告/第三季度报告，复用精确公司身份/分页/源429停止/15秒/缓存。只完整报告，摘要英文无效；更正修订等保留pending_review不自动解析，带fiscal_year/period_months/period_end、kind=interim_report，尚不确认latest。查询完成只代表本次关键词范围，不代表财报最新已核实。

新增interim_extract.extract_interim_text(pages,*,symbol,source_url,disclosed_on,as_of)，返回financial_extract兼容base字段financial_proposals及period信息。识别Q1/H1/Q3，使用year_to_date累计基础，period_months=3/6/9、period_end对应月末。current和明确比较列分别产year/previousyear，比较项保留comparison_basis=current/previous_unadjusted/previous_adjusted_after；重述comparison_requires_review。只支持明确人民币单位/列布局/严格数字token；Q3只选年初累计，不用当季当累计。Q1/H1支持3列普通，调整布局须明确调整后/前顺序和4列；Q3支持4列普通（单季金额/增幅/累计/增幅，仅当前累计）及已探针8列调整布局（当前累计索引4、上年同期调整后索引6）；不适用占位仅允许非目标季度列。模糊/多表/冲突/未知单位/未来/身份错误保持pending，金额不猜。页码/摘录/原始列及PDF摘要留档。

financial_extract.extract_archived_financials增加可选text_extractor回调，默认仍年报函数；复用相同25MiB/1000页/首20页/路径/hash/签名边界。新extract_archived_interims包装该执行器。

### 2. TTM纯计算与来源

新增interim_ttm.prepare_ttm(symbol,evidence,as_of)->dict，独立纯函数，不导入financial_facts以避免循环。数据来自financial_proposal年度和quarterly_financial_proposal累计建议、quarterly_financial_document(含失败文档期末)、admission_reporting_coverage。

季度fields延续年报metric/amount_yuan/currency/scope等，新增fiscal_year、period_months(3/6/9)、period_end、period_basis='year_to_date'、comparison_basis；披露须晚于period_end且<=as_of，来源/身份/元单位及审核状态校验同年报。无审核raw可计算研究建议，明确驳回/过期/未来审核不可用。等值合并、冲突None，Decimal精确加减用_sum_amounts，不把负/零丢掉。

TTM=FY(y-1)+YTD(y,m)-YTD(y-1,m)，仅3/6/9月累计，不以年报直接回填TTM。归母/扣非分别计算，收入/OCF可作为研究metric但不自动启用成长阈值；每项给三个分量/来源/计算式/amount/status。选择最新已知报告period_end（所有合法身份的季度proposal、document及coverage中的max），新期间缺数据/失败不得回退旧完整期间来声称最新。没有季度period返回None，年度只保留annual。

最新和可比范围需要显式已审核admission_reporting_coverage：payload={period_end,coverage_through:as_of,report_types_complete:true,scope_comparable:true,basis:非空}，有效来源/90天审核、无同组冲突，且period_end等于最新已知期末。只有这份证据及分量均齐全才latest_announced=true；否则数学结果可展示但latest_announced=false，不能放行TTM。coverage事实不能由查询完成自动确认。冲突coverage也不能选有利版本。

返回稳定version/symbol/as_of/period_end/period_months/disclosed_on/metrics（key parent_profit_yuan/deducted_parent_profit_yuan/revenue_yuan/operating_cash_flow_yuan，每项status/amount_yuan/sources/components）/latest_announced/coverage_evidence_ids/dependency_inventory/unresolved/requires_review/formalfalse。来源refs与financial_facts.field_sources相同追踪基础，包含evidence_id/review_id/source_url/content_sha256/disclosed_on和rawfields/origin/原值，不伪造PDF。

### 3. 数据库与年度准备器接通

research_store.import_review_packet支持quarterly_proposals/documents到quarterly_financial_proposal/document，不改变原financial_proposals；文档记录period_end允许失败报告阻止旧期间回退。identity payload对quarterly_proposal同样fields/origin。admission_reporting_coverage作为显式独立事实通过既有add/review保存。

financial_facts.prepare_financial_facts为profit组附prepare_ttm结果：ttm={period_end,disclosed_on,parent_profit_yuan,deducted_parent_profit_yuan,latest_announced}，额外ttm_sources/ttm_summary保存完整算式，不把raw建议变审核；无季度数据仍None。cash组仍年度且scopefalse。

只profit有季度相关资料时_dependencies.version=2，inventory包括本公司全部financial_proposal/quarterly_financial_proposal/quarterly_financial_document/admission_reporting_coverage的ID和审核版本；cash和纯annual保持v1。验证器读取所有这些原文及security，对version1/2相应名录检查，再按同准备器完整重建。v1旧利润遇新季度资料重建不一致即失效；不能用旧annual覆盖新亏损或绕过latest。无deps既有手工事实保持原审核边界。

### 4. CLI与端到端

新增interim-discover/interim-extract复用既有发现与归档输入保护；interim-review-packet将提取结果包装为可导入quarterly_proposals/documents审核包，带内容hash和未核实标志（不自动导入/确认）。原stock-financial-facts-prepare命令可同时准备年度和季度TTM，不增加自动启用选项。可沿用dividend-materials/archive命令下载发现的documents；资源预算和source429不变。

测试先行。解析代理只interim_extract.py/tests+三份资源；TTM代理只interim_ttm.py/test_stock_interim_ttm.py；父发现/共享执行器/存储/组装/CLI/集成/映射/文档。S级独立双审查及复审；真实三份PDF提取/packet/合成库年度+季度TTM保存、coverage未审不能最新/上游新报告失效。实际库不替用户审核或入池。verify按相关范围，不全量、不重复UI验收。完整事项分为可独立审查两笔本地提交：季度发现提取审核包；TTM计算及存储来源接通。过程中持续推进不等逐项确认；不推送、不发布成员。

### 第一笔实际验证（2026-10-15）

- 官方三份完整PDF经共享归档wrapper重验均matched_template，每份8条建议，共24条；产物tmp/stock-interim-probe/extracted-explanation-recheck.json。
- 真实Q1/Q3变动说明与页眉曾污染表识别，已修准确表头及非目标行边界，并保留真多表拒绝。单位/币种全部声明须一致，不能只挑首个。特殊标题词与发现器对齐。
- 发现/PDF双向期间错链均清空建议，记录两个期末并取合法较新期末作失败文档阻断线索；非金额依据。
- 审核包导入本地验收库新增29条，stocks_companies=685、stocks_evidence=801、stocks_reviews=0、stocks_source_batches=4；members_published=0。统计第一次误用无前缀表名失败，已通过sqlite_master确认后重查。
- 修改测试映射以覆盖解析器、命令、共享wrapper及季度资源。相关检查结果随提交记录补充；未运行全量回归，未重复无改动的UI验收。

第一笔门禁：verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv：Ruff通过，Pyright 0错误，517项相关测试通过（64.60秒），测试目录已清理。独立规格/质量审查发现项已收敛，第一笔复审通过。未运行全量回归。

### 第二笔真实资料只读探针

第一笔导入后，stock-financial-facts-prepare对600900读取年度和三份季度建议，得到2026-06-30 TTM：归母36202151477.37元，扣非34549413234.25元。分别为2025全年+2026H1-2025H1；完整来源保存在tmp/stock-research-acceptance/financial-facts-interim-cypc.json。原文存在比较重述，尚无独立覆盖/可比审核，因此latest_announced=false、未保存或审核。利润依赖v2，现金依赖v1。旧不可变评估保留，freshness.current=false，原因source_revision_changed:600900；没有覆盖旧评估。

纯TTM规格审查发现真实失败文档无PDF摘要时会丢新期阻断线索，正在补真实packet→SQLite回归；期间线索与金额来源校验必须分开。季度comparison_basis限定当前/上年同期未调整/上年同期调整后，拒绝单季度等自相矛盾元数据。

第二笔完成：纯模块及SQLite集成158项测试通过；最终verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv：Ruff通过、Pyright 0错误、335项相关测试通过（53.67秒），临时目录已清理。独立规格和质量最终PASS；质量探针2436次坏类型/数值变异无未捕获异常。真实只读探针最终重跑结果相同，利润v2绑定51条上游，financial_verified=false、saved=null。未运行全量回归，正式成员0。

### 2026-10-18全范围推进与短标题修复

研究截点仍固定2026-10-17；本次抓取实际发生于2026-10-18，不声称已取得历史时点快照。既有626家公司范围不变，先处理资料覆盖，再判断选股资格。检测到此前年度采集及后续离线整理进程均已退出、状态文件仍停在等待，故从检查点恢复；年度采集已处理626/626，复用620家，新增6家，失败及导入失败均0。随后续跑既有离线重解析命令，保留原文与全部待审边界。

先用TCL科技、美的集团、宁德时代、招商银行、贵州茅台、长江电力、金山办公、中芯国际8家公司运行通用interim-discover，原查询发现66份报告。发现部分深市公司只有半年报，不能将query_scope_complete解释为报告齐全。对同一官方接口补查“一季度报告”“三季度报告”，真实返回中文“2026年一季度报告”“2025年三季度报告”；此前带“第”的查询只返回英文报告，且标题解析不接受短写法。

小范围修复：季度关键词改为“一季度报告/半年度报告/三季度报告”，真实接口同时返回带“第”的英文标题及不带“第”的中文标题；发现与PDF标题识别均允许可选“第”，报告期归一，保留英文/摘要排除、修订待审、公司身份及披露日验证。修复不增加查询次数，也不放宽财务条件。独立研究日期及最新报告覆盖仍必须核验，不能仅凭关键词查询完成声称最新。

离线资源`tests/resources/radar/cninfo-000100-interim-titles.json`来自2026-10-18上述两次查询，各保留前两条原始公告对象（每次一条英文、一条中文），其余记录裁去；保留HTML高亮、标识、公司代码、日期与路径字段，以真实格式复现遗漏。完整响应留在`tmp/quarter-discovery-20261017/short-title-probe-*.json`，资源映射至公告与季度相关测试。另用已有真实会计数据文本只替换标题的变异测试，验证期间与金额均不改变。

按B级小修由主会话核对diff，不派代理。测试先行出现真实短标题拒绝及新测试调用记录形状误用，修正后公告发现、季度提取/审核包、共享分红及CLI相关146项通过；资源映射单独1项通过，全部改动Python文件Ruff/Pyright通过。未运行全量，无UI改动。真实8家复核全部查询完成、来源未429停止，报告数从66增至89，增加23份；仍未核验报告完整性、可比性或金额真实性。

已用同一缓存启动626家公司季度公告发现：`radar interim-discover --input tmp/quarter-discovery-20261017/scope.json --output tmp/quarter-discovery-20261017/scope-result.json --as-of 2026-10-17 --cache-dir tmp/quarter-discovery-20261017/cache`。本步不下载季度PDF、不自动审核、不发布，不启用周期更新；逐请求原始缓存可在中断后由同一命令复用。日志`tmp/jobs/quarter-discovery-scope-20261018.*.log`。全范围完成前不把8家结果推广为626家结果。

#### 结构化财务来源只读探针

为减少PDF逐份适配，按当前已安装AkShare源码中的实际请求定义，对东方财富TCL科技指标接口取8期，另对2025年利润表与现金流量表各取1条，三次均HTTP200。原始响应、请求参数、抓取时间、SHA256留在`tmp/financial-source-probe-20261018/`；仅研究，不写库。指标接口为`datacenter.eastmoney.com/securities/api/data/get`的`RPT_F10_FINANCE_MAINFINADATA`，利润/现金流接口为`datacenter-web.eastmoney.com/api/data/v1/get`的`RPT_DMSK_FN_INCOME`、`RPT_DMSK_FN_CASHFLOW`。

指标接口有CURRENCY、NOTICE_DATE、UPDATE_DATE，但仅每股现金流及比率，没有已核验现金流总额；利润/现金流原始行缺明确币种字段。2025年指标归母4516782000元、扣非2896878481元；利润表归母4516782000、扣非2896877070；现金流表NETCASH_OPERATE为44021698000，归档年报现金流为44021698580。TOTALOPERATEREVE为营业总收入184210915000，不能等同此前年报表的营业收入184063390556。数值精度、字段口径及重述版本尚未完全对齐，不能自动将差异判为数据错误或选有利值；不采用每股现金流估算总额。此探针尚不足以建立正式筛选数据契约，未导入任何财务建议或审核事实。


<a id="source-2026-10-15-stock-radar-market-context"></a>

## 2026-10-15 第一阶段：统一股票市场日期窗口

> 来源：`docs/superpowers/plans/2026-10/2026-10-15-stock-radar-market-context.md`；整理前版本 `ad626dc`。


用户授权持续实现无需逐项确认，延续已批准数据/规则阶段；不新增投资门槛、不启用正式筛选或后台自动更新。已读design-before-implementation，按该明确授权执行。

### 已知事实与取舍

准入/风格/评估归档CLI当前允许输入研究日期列表，缺少从现有交易日历生成标准窗口的通用入口。现有radar.calendar.TradingCalendar有只读refresh_enabled=False、cached_data、境内15时收盘与覆盖边界。日历失败不能推断周一至周五，股票停牌不能补更早行情。现有data.akshare.get_total_shares会降级流通股本/财报反推，不能作为全股类市值审核依据。

### 行为与接口

新增radar.stocks.market_context.prepare_market_window(calendar,as_of,*,now)读取一次cached_data，不联网、不建表/更新缓存。只支持XSHG（现有共同境内上市日期口径），未来时间或无时区now拒绝。研究日不能晚于北京now日期；effective_at取研究日北京日终与now较早者，交易日当日15时前排除当日，周末/假期用缓存明确列出的上一个收盘日。覆盖必须包括effective_at的日期，且此前至少60个明确交易日；不足不补工作日、不挪动选择截止日。

输出version=stock-market-window-v1、market=XSHG、as_of、effective_at、days（递增唯一60个日期）、latest_completed、calendar{source,fetched_at,coverage_start,coverage_end,snapshot_sha256}、research_only=true、formal_screening_enabled=false。snapshot_sha256是完整日历记录规范JSON摘要，不冒充原始供应商响应摘要。拒绝无效/未来fetched_at、空source、异常日历覆盖；无额外联网预算。

新增radar stock-market-window --calendar-db 已存在路径 --as-of 日期 --output JSON（必填），只读实例，输出不能覆盖DB及WAL/SHM/journal。新命令注册原CLI。不提供refresh以免读取隐式联网。现有stock-admission/style/evaluation的_read_market_window接受新envelope及旧日期列表；envelope必须版本/market/as_of匹配，其他审核与research_context_only边界不变。输入JSON仍是研究上下文，不是可伪造的已核实准入事实。

### 实施与验证

A类新模块由实现代理完成market_context.py、stock_market_context.py及新测试，父完成解析器兼容、注册、映射/文档；独立合并规格质量审查。测试先红：真实SQLite日历只读/no网络/no副作用、长假/盘中盘后/UTC/历史、覆盖不足、<60、未来/坏时区、输出保护、CLI互通和旧列表兼容。复用已有真实本地日历作只读验收；无有效覆盖则如实记录不伪造。新增模块及测试映射verify限定范围，验证后完整事项本地一笔提交，不推送。此项不是第一阶段全部完成，也不是新交付阶段。

### 实际只读验收与兼容

现有.stock_robot/radar_collector.db含8797个日历记录，覆盖1990-12-19至2026-12-31，fetched_at=2026-10-14T13:59:43.919836+08:00，source=akshare:sina:XSHG。只读命令2026-10-15T15:10生成60日2026-07-16至2026-10-15，snapshot_sha256=4a87b6877fe87165c2a1bfbdda1874d1b760524f69ac831fe1c0d3a63fefcc8b；没有为此联网或刷新日历。

stock-style-evaluate成功读取envelope，仍为研究上下文；stock-evaluation-capture用同窗口为600900生成新不可变档案68fbe32e316997511768c700f200e2bef9a70c6d687c70c2c91303099851aeed，status=complete仅计算完成，freshness.current=true，formal_screening_enabled=false、members_published=0。旧档案不覆盖。验收JSON仅留tmp。

新增envelope兼容先红1失败（旧解析仅接受列表），实施后相关51测试通过；其中新模块28项、原CLI及envelope23项。实现代码Ruff/Pyright通过。最终门禁及审查结果后补。

SQLite只读边界：命令不更新业务日历、不建表或刷新来源。标准SQLite只读连接在WAL模式下可能创建WAL/SHM辅助文件或维护读锁；不承诺完全零文件变化。保留正常WAL读取，不用immutable忽略尚未checkpoint的已提交更新。新增活动WAL回归验证能读取新source/新交易日/新快照摘要，且读取前后业务行和主库页不变。

最终验证：verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv，Ruff通过、Pyright 0错误、375项相关测试通过（51.94秒）；后补活动WAL测试单独1通过并复核Ruff/Pyright。未因仅新增测试与说明重复门禁，未运行全量。A级独立合并规格质量审查最终通过。直接单文件Pyright虽0错误仍提示工作树无本地.venv，正式门禁已绑定共享虚拟环境；不把环境提示藏掉。


<a id="source-2026-10-15-stock-radar-market-history"></a>

## 2026-10-15 第一阶段：股票原始日线与成交额研究适配器

> 来源：`docs/superpowers/plans/2026-10/2026-10-15-stock-radar-market-history.md`；整理前版本 `ad626dc`。


用户要求持续实施无需确认，沿用design-before-implementation与已批准资源预算；不增加投资阈值，不发布成员，不启用自动更新。属于数据接通，非新增交付阶段。

### 探针事实和方案取舍

- 已读安装版ak.stock_zh_a_hist：东财HTTPS kline，klt=101/fqt=0；真实请求被ConnectionResetError10054重置，不能声称源可用。
- 已读安装版ak.stock_zh_a_daily：SDK会outer merge流通股本、ffill、drop_duplicates，不能用于严格60日真实记录；不调用该封装。
- 直接新浪URL https://finance.sina.com.cn/realstock/company/sh600900/hisdata_klc2/klc_kl.js 实际200、65705字节，decoded 5138条，最后2026-10-15 close29.6/volume138200939/amount4072104696，另有postVol/postAmt，均保留不合并猜测。
- body首部var KLC_K2_sh600900="..."; 已确认身份可绑定。可信解码常量akshare.stock.stock_zh_a_sina.hk_js_decode，py_mini_racer.MiniRacer.call支持timeout和max_memory。上下文eval可信常量、call把编码字符串作为数据传入；绝不eval服务器JS。
- 生产解码在现有bounded_provider_call子进程中，deadline15秒；V8 eval/call各2秒限制、max_memory=64MiB。复用已安装AkShare/mini_racer，不加依赖。先用该已实测可用来源；东财不引入故障源强制重试链。
- 官方AkShare文档 https://akshare.akfamily.xyz/data/stock/stock.html 历史日线章节说明未复权/日成交额元；新浪章节amount元、volume股。此适配器按原始解码字段，不把复权或流通股本带入。未复权价格不能直接当总收益或趋势复权序列。

### 模块与契约

新增radar.stocks.market_history：sina_url(symbol)固定HTTPS主机与路径，只支持现有沪深股票_symbol；decode_sina_body(raw,symbol)->list使用完整assignment正则核对变量身份和JSON字符串，不执行响应。parse_sina_history(raw,symbol,market_window,*,decoder=None)解析已知解码行、校验日期（YYYY-MM-DDT00:00:00.000Z视为源交易日期而非逐笔时点）和OHLC正值范围、volume非负、amount非负，数值Decimal字符串；保留postVol/postAmt及原始行。仅目标window内数据进入rows；重复目标日全部标未知/冲突不取有利条，缺失日不补零/不ffill/不挪更早行情。返回rows、missing_dates、diagnostics、window_complete、currency=CNY、price_basis=unadjusted、amount_basis=source_raw_turnover，research_only/financial_verified=false/formalfalse。窗口须60递增唯一合法日期。

collect_stock_history(companies,market_window,cache_dir,*,as_of,refresh=False,http_get=None,sleep=...)批量串行请求（<=已批准并发2），每次timeout15、最多重试2次、请求间隔1秒、429立即停止此来源后续网络。响应stream读取上限4MiB，禁重定向，必close。HTTP4xx非429不重试、5xx和requests/OSError重试。单公司失败隔离，初始输入/窗口一次全校验再建目录；不因一家公司成功将整批标全成功。

缓存body按真实SHA256命名<sha>.js，manifest按URL/symbol/asof/window/version摘要命名，原始数据刷新不删除旧blob；缓存命中重验字节/hash/身份并重跑当前解析器，禁止符号链接/路径逃逸、坏摘要重抓。失败刷新不能把旧cache当最新成功。缓存1GiB软提醒不删除。写入原子；不将完整响应、缓存、日志提交。

输出items逐公司status=downloaded/cached/source_error/source_stopped、url、content_sha256、fetched_at、parse结果；status=complete/partial仅采集执行状态。只在60日真实记录全部合法时生成liquidity_fact={kind:style_liquidity,payload:{currency:CNY,scope:a_share_raw_turnover,daily:[date,amount_yuan],price_basis:unadjusted,amount_basis:source_raw_turnover,as_of,fetched_at},source_url,content_sha256,disclosed_on:实际采集北京日期}，并requires_review=true。disclosed_on不伪装历史公告日，历史研究若采集晚于截点不能用作当时已知事实。该fact仅导出供后续显式证据添加/审核，不写DB或自动确认。市值不推算、复权/基准/趋势另行处理。

新增stock_robot.stock_market_history命令stock-market-history --input companies.json --market-window JSON --as-of --cache-dir --output [--refresh]。复用_read_market_window和候选规范；输入/窗口/输出不能互相覆盖或位于cache内，检查在任何网络/写入之前。默认无DB写入，注册CLI由父处理。

### 分工与验收

S级跨API：实现代理仅market_history.py及test_stock_market_history.py；父CLI/CLI测试/注册/映射/文档及真实网络。独立规格、质量双审查/修复复核。测试先行：真实3行样本(仅裁解码后末3日，不是完整下载响应)、非法身份/注入字符串、重复/缺失/零成交/坏OHLC/金额单位/源post字段、不变输入；HTTP预算/429/禁redirect/size/close、cache摘要破坏/符号链接/刷新不可覆盖旧blob/部分失败/不碰DB。V8依赖原有已安装，无新增安装。

实际原文与完整解码仅tmp/stock-market-history-probe，采样2026-10-15；tests/resources/radar/sina-600900-2026-daily-sample.json为末3条解码行，完整JS摘要见tmp/sina-request.json，正式验证后补。verify映射明确；用真实600900采集→缓存重用→生成待审流动性fact，不审核/不入池。相关检查通过完整事项本地提交；不全量、不重复UI验收。

### 原文摘要与补充实现决定

真实新浪完整JS SHA256=88620bac9829a4c357918db16014ed9e753f3bf78bd2479b44820a11fe687812（65705字节）；裁切样本只有末3条解码记录，原始响应/完整解码留tmp，不提交。MiniRacer当前导入from py_mini_racer import MiniRacer，eval/call timeout=2000/max_memory=64MiB探针通过，不能用旧py_mini_racer子模块入口。

HTTP requests timeout只限制socket等待，不能限制慢速流整体时长；因此生产完整GET/流式读取/关闭响应也放现有bounded_provider_call，单次15秒总deadline；注入fake HTTP仅用于测试，正常CLI不会绕过隔离。429必须返回状态后由父流程停源，不能扁平为普通重试异常。解码单独15秒进程deadline，两步V8各2秒。

父审发现畸形manifest JSON若为列表/null可能在逐公司隔离外抛AttributeError，已交实现收窄类型并将缓存异常保留为可重抓/逐公司失败。CLI先红ImportError（模块未实现），实现后11项输入保护与注册测试通过。

### 真实验收、异常及修复

正式CLI第一次返回source_error：真实JS赋值后还带单个281字符完整块注释，严格正则原先未支持；不把来源失败说成成功。修复仅允许该完整惰性尾注释，嵌套/未闭合/多块/额外执行仍拒绝，服务器JS从不eval。实际65705字节probe经身份校验通过。

源200响应在解析前按SHA留blob，即使身份或解析失败也保留raw_artifact、摘要和fetched_at，错误manifest仍source_error、无成功缓存和事实。生产过大/类型错误改结构化非瞬态不重试，超时/网络错误仍最多三次；429不扁平化。

真实stock-market-history重新执行：600900 status=downloaded、window_complete=true、60日/缺口0，2026-10-15 close29.6、raw amount4072104696元、postVol68000/postAmt2012800原样保留未相加；完整JS摘要与前述相同，fetched_at=2026-10-15T12:58:00.796850+00:00。第二次status=cached，原摘要和fetched_at保持，重新解析，不额外联网。

通过真实stock-evidence-add将导出的style_liquidity显式留档为pending，evidence_id=af62cf4f50c0196fd807a4cf724a0c085c80a888812fe54e2d80660917cacf95，600900 revision5。库companies685/evidence802/reviews0/batches4；两池重新计算的liquidity均unavailable，证明未审资料不放行；未发布成员。旧评估按来源版本自然失效，不覆盖历史。

质量审查追加实读上限防stat后文件增长，非JSON/NaN辅助字段导致该日未知而不丢其他公司输出，缓存stat/iterdir错误只提醒不推翻已完成采集；修复后70项测试通过，1项Windows符号链接权限跳过，Ruff/Pyright0。CLI11项测试通过。只涉及CLI与数据层，无UI变更，无需重复浏览器验收；不运行全量。

最终门禁：verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv，Ruff通过、Pyright 0错误0警告、255项相关测试通过/1项Windows符号链接权限跳过（51.82秒），成功目录已清理。规格审查通过；质量三项修复定向复审通过，无剩余阻断。不运行全量，不推送或发布。


<a id="source-2026-10-15-stock-radar-official-roster"></a>

## 2026-10-15 第一阶段：官方沪深名录与上市日线索

> 来源：`docs/superpowers/plans/2026-10/2026-10-15-stock-radar-official-roster.md`；整理前版本 `ad626dc`。


用户授权持续实现无需确认，沿用design-before-implementation；名录是候选来源，不是成员、龙头或经营质量证据，不新增投资门槛。

### 探针事实

2026-10-15 SSE https://query.sse.com.cn/sseQuery/commonQuery.do，sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L、STOCK_TYPE1主板、8科创，其他params参照tmp/stock-security-source-probe/manifest.json及已安装stock_info_sh_name_code。主板200/1946691字节/1702条；科创200/715290字节/618条。pageHelp.total为实际总数，metadata.pageSize2000/pageSizeWithOutLimit10000；首版只在一次result覆盖total时认定源完整。科创含689009 CDR，AkShare官方文档说明其不属普通A股，需明确排除；其余未知代码不得静默排除后声称完整。

SZSE https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1110&TABKEY=tab1，200/545393字节/2906条。工作表A股列表，实际22列，dimension却A1；openpyxl只读后reset_dimensions已验证。zip9项/展开4154409字节。openpyxl>=3.0.3已是AkShare核心依赖，不新增组件。

SSE LIST_DATE可供A股上市日；CSRC_CODE仅行业大类，J不足以区分金融类型。SZSE仅A/B股文本，无H类与明确业务截点/倍率，不将A股总数当公司全股类。issuer_type默认None；采集时间不冒充资料业务日。原始表仅tmp。

### 模块契约

新增radar.stocks.official_roster：parse_sse_roster(raw,board)->dict，board为sse_main/sse_star；parse_szse_roster(raw)->dict；collect_stock_roster(cache_dir,*,as_of,refresh=False,http_get=None,sleep=...)->dict。

parser返回rows(symbol/name/a_listed_on或None/industry_source_code/industry_source_label/source_fields原行)、declared_total/row_count/scope_exclusions/diagnostics/complete。SSE核sqlId/stock_type/list_board/市场代码、错误包、total与实际唯一数；总数不齐或未知身份则partial，可保留合法线索，绝不猜分页完整。重复code全部排除并诊断；日期未知保留None。只有明确689009按CDR范围排除，其余未知代码导致不完整。

SZSE先zip预算：压缩4MiB、展开20MiB、64entries，拒绝加密/异常路径；只读A股列表并reset_dimensions，唯一必需header，最多64列/20000行，公式不能当事实。代码须六位字符串，不猜补零；日期严格已知格式。股数原文本保留不换算、不算市值。无源total，仅说本次下载表完整解析，不保证历史成员。

合并三个必需source：SSE主板/科创/SZSE。跨源冲突不择优，universe_complete仅全部源完整且无未知身份/冲突时true。companies可直接stock-records-import --kind candidates，admission_status=pending_verification；research_hints含原行/上市日/原行业/URL/hash/fetched_at。可附security_proposal，exchange/security_type ordinary_a/a_listed_on/issuer_typeNone，disclosed_on真实采集北京日，默认仅导出不写DB或审核。formalfalse/members_published0。原行业不是申万两级或龙头事实。

### 请求与缓存

当前接口不是历史成分，仅当前北京日允许网络；历史as_of只复用当日抓取缓存，缺缓存partial，不取今天名录假装历史。生产GET+stream已有bounded_provider_call15秒总deadline，4MiB上限、必close、禁redirect；网络/5xx重试2次，非瞬态错误不重试，429按host停止（SSE主板429跳科创但可继续SZSE）。串行间隔1秒，不超并发2。生产解析也15秒子进程，zip预算如上。

内容寻址blob+版本/source/asof manifest；命中实读限长、摘要、URL、抓取日有效，重跑当前parser；刷新保留旧blob，失败不回用旧成功。root/文件拒符号链接，坏cache/IO逐源隔离，1GiB软提醒不删除且统计异常不推翻结果。原子保存。不重构已完成行情模块。

新增stock_robot.stock_official_roster命令stock-universe-discover --as-of --cache-dir --output [--refresh]，无DB参数仅导出，输出不可位于cache内。父CLI/测试/映射/文档/真实验证；实现代理仅official_roster.py及测试。

### 验证

S级先测试、双审与复审。资源sse-600900-601318-listing.json/sse-688001-listing.json裁真实result、保留真实total所以默认partial，正向完整测试显式合成total；去pageHelp.data重复全表。szse-000001-000333-listing.xlsx裁实际两行22列，包装重现dimensionA1；采样2026-10-15，完整响应/metadata只tmp/stock-security-source-probe，不加README。

覆盖身份/total/重复/未知日期/CDR/历史不得network/429跨source/预算close/坏cache/zip预算/dimension/公式/股数不推算/行业不当龙头；CLI与现有候选导入真实SQLite互通，未审不放行。真实三源采集与cache复用，按变更verify不全量，完整事项本地提交不推送。

原始响应SHA256：sse-a-list.txt=64ce2e2ce67428ce2fe816e82ac2316749b5b1213e982c5f512f95083df4d32d；sse-star-list.txt=12e1f96294b990b4182eb4faa053115c45405ec2de0e8ba1fd3d692ecc2744ff；szse-a-list.xlsx=65c456e1703ce62985027820cb65e93abc12d9728ff46f3240043d65196b7a8a。原型plan写入第一次审批处理超时CreateProcess未启动，按工具许可重试一次后成功，并非安全拒绝。CLI先红ImportError因模块尚未实现，保留红态诊断目录。

### 实施、审查与真实验收

双审关闭：真实单元格坐标检查稀疏第66列/20002行，防max_col/max_row静默截断；Excel datetime先从原cell解析上市日，ISO原值另保留；坏行也参与原始合法code重复计数，不择优；CDR排除前所有dict严格JSON校验，含真正原文字节1e999浮点溢出；SSE actionErrors/fieldErrors即使result足数仍拒完整。顶层status与universe_complete同一变量，financial_verified=false。

CLI未来日期初测未在调用域函数前拒绝（_cutoff只负责解析），父补北京时间未来日期预检；CLI6项通过。实现53项后追加overflow与请求头回归，最终相关门禁另记。一个Windows符号链接权限测试跳过，代码路径仍有拒绝和其他回归。

第一次正式三源：SSE主板1702、科创617（另CDR1）成功，SZSE403；整批partial/False，候选2319，没有冒充全市场完成。原因所有source误共用SSE Referer，已按已安装各SDK真实契约拆分请求头：SSE使用其公开名录Referer+UA，SZSE默认headers={}，生产与fake路径统一，回归覆盖。修后无需重抓已成功SSE缓存，SZSE200/2906。

正式CLI最终status=complete/universe_complete=true，companies5225；上市日600900=2003-11-18、601318=2007-03-01、000333=2013-09-18；issuer_type均None。第二次三source全部cached，hash/fetched_at不变并重新解析。raw原文、导出JSON和cache只tmp/stock-official-roster-cache及tmp/stock-research-acceptance。

显式stock-records-import --kind candidates实库往返：新增4540家公司/5225条候选线索；研究库companies5225、evidence6027、reviews0、batches5；members_published0。示例600900 revision6、601318 revision2、000333 revision3、688001 revision1，均无已确认事实；旧候选保留并附新原文，不自动审查或写入正式成员。该5225是数据来源候选范围，不是股票池正式成员数。

源中未来DELIST_DATE及ST/状态代码等原字段完整保留，未把名录普通A候选解释为经营或上市状态风险已核实。业务日期未知不伪造；全股类历史股本与公司市值仍独立待接通。实际SZSE源缺默认样式触发openpyxl已知UserWarning，仅读取值不影响结果；没有修改依赖或隐藏异常。

最终暂存范围门禁：.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv，Ruff通过、Pyright 0错误、pytest 233 passed / 1 skipped（45.86秒）；跳过项为Windows符号链接权限，成功测试临时目录已清理。双审最终均通过。


<a id="source-2026-10-15-stock-radar-research-persistence"></a>

## 2026-10-15 股票共享候选与证据审核持久化实施计划

> 来源：`docs/superpowers/plans/2026-10/2026-10-15-stock-radar-research-persistence.md`；整理前版本 `ad626dc`。


日期2026-10-15。用户明确选择完整股票池并授权继续；按已确认共享候选、证据审核、90天复核及版本冲突设计实施，不重新确认技术细节。本阶段具有独立价值：研究资料可以进入可持续审核的业务记录，而不是继续堆离线JSON。未实现的成员发布、准入计算和生命周期服务不冒充完成。

### 1. 事实、边界与存储

沿用独立工作树codex/stock-radar-pools和主仓库共享.venv。工作树无.codegraph，直接读当前文件；主索引已经CodeGraph调查。现有RadarStore和CollectorStore共用本地SQLite，连接使用contextlib.closing，stocks表不能改ETF表或PRAGMA user_version。新增stocks_schema_versions、stocks_source_batches、stocks_companies、stocks_evidence、stocks_reviews，研究来源、证据与审核不可变长期保留。

所有资料导入保持pending_verification；输入里financial_verified或status不能赋予审核权。候选共同准入与风格仍未知，导入和证据确认均不产生成员。数值规则未配置、ETF配置和空股票池API保持当前状态。本阶段不加UI/API写路由、后台任务或新依赖。

公司symbol共享且唯一，多个批次可补充同一公司资料。批次ID绑定导入种类、数据截点及输入SHA；相同批次重跑幂等，不增加公司revision或审核。旧截点可留档，但不覆盖更新截点的展示资料，不影响已有审核。证据ID绑定公司、证据种类、原文追踪及内容，来源／字段变化产生新ID，不能把旧审核转移到新证据。导入原始批次和每条payload保存，不把供应商提示当事实。对输入非有限数字转为文字线索，最终JSON不含NaN。

SQLite每操作独立连接、WAL、foreign_keys=ON、busy_timeout5秒；写事务BEGIN IMMEDIATE。schema版本使用本模块专用表，可重复初始化；未知较新版本拒绝。空db合法，同库ETF表内容必须保持。每次涉及公司资料或新证据的成功写入递增公司revision一次；读／完全重复导入不递增。

### 2. 模块接口

新增 `src/radar/stocks/research_store.py` 和 `tests/radar/test_stock_research_store.py`，由实现代理完成，主会话写CLI和映射。

- `StockResearchStore(db_path:Path, *, clock:Callable[[],datetime]|None=None)`，clock默认UTC aware，注入naive应拒绝。
- `import_companies(records:list[dict[str,Any]], *, source_sha256:str, source_label:str, data_as_of:date)->dict`。任意候选列表，精确沪深A股symbol，name/current_name可缺用symbol；完整原始行作为kind=research_hint证据，默认不可确认。一次批次原子写入，坏公司/重复symbol整批拒绝，不能部分写入。返回batch_id/companies_added/evidence_added/reused。
- `import_review_packet(packet:dict, *, source_sha256:str, source_label:str, data_as_of:date)->dict`。companies列表；导入每公司proposals、financial_proposals、documents、financial_documents和未解决事项为独立证据。不信任packet审核标志或proposal_id；取fields和origin内容并保存完整行。原始文档失败也作为线索保留。规范evidence字段只能作为research_hint，不能直接confirmed。原文追踪从origin取source_url/disclosed_on/file_sha256；无有效原文hash或日期不补猜。
- `add_evidence(symbol:str, *, kind:str, payload:dict[str,Any], source_url:str, disclosed_on:date, content_sha256:str, expected_revision:int)->dict`，允许用户显式补充原文证据（不限于财务，kind非空）。来源必须HTTPS/无凭据/非localhost或IP，披露日及data_as_of不得晚于当前北京时间日期。hash64位小写；当前行保持pending。无公司拒绝，过期revision拒绝，重复同证据不增加revision。
- `save_review(symbol:str,evidence_id:str,*,expected_revision:int,decision:str,reviewer:str,reason:str)->dict`，decision只允许confirmed/rejected/needs_evidence。确认需证据具有有效HTTPS来源、64位原文hash及有效已披露日期，research_hint和失败documents不能靠输入标志确认；导入的实施／财务建议具有完整原文追踪时可由显式审核确认，但这不确认普通股或整家公司资格。reason/reviewer非空，审核日由服务clock产生而非输入冒充。审核记录只追加并绑定证据ID与内容，原文变化后不复用。审核更改递增公司revision，旧revision抛StockRevisionConflict。
- `get_company(symbol:str)->dict|None`，包含symbol/name/revision/data_as_of，admission_status始终pending_verification。
- `list_companies(*,search:str='',page:int=1,page_size:int=15)->dict`，查询全候选名／代码，SQL转义%/_，稳定按symbol排序，返回items/total/page/page_size。非法页码拒绝，页面大小不添加任意业务上限。
- `list_evidence(symbol:str)->list[dict]`，返回原文payload、来源、当前最新审核和review_status；confirmed自reviewed_at起90天内有效，超过则expired；未来审核不生效。rejected/needs_evidence明确区分，不删除旧review。
- `review_history(symbol:str)->list[dict]`按追加顺序，`summary()->dict`公司/证据/审核总数。可提供内部connect/helper，不外部暴露手工修改事实通过准入的接口。

领域异常：StockRevisionConflict(ValueError)和StockResearchError(ValueError)（如需要），便于后续API映射409/422。结果均JSON安全；日期时间为ISO带时区。记录不因旧批次/源失败恢复成员；本阶段没有成员写入口。

### 3. CLI与验收

主会话新增stock_robot.stock_research CLI：stock-records-import(--kind candidates|review_packet,--input,--db,--as-of)、stock-records-list(--db,--search,--page,--symbol可选公司详情及审核历史,--output可选)、stock-evidence-add(--input,--db,--expected-revision)、stock-evidence-review(--db,--symbol,--evidence-id,--expected-revision,--decision,--reviewer,--reason)，注册radar分组。db显式指定；保护输入／输出不覆盖db和-wal/-shm。import读取候选companies或原始列表、review包；原始输入SHA和路径留档。CLI不提供直接资格通过或成员发布。

测试先行：同批幂等、同股多批、旧批不覆盖新资料、跨公司证据拒绝、无来源不可确认、输入自称verified仍待审、审核不变资格、90天边界、更新后旧审核不迁移、两连接revision竞争一胜一冲突、整个导入原子回滚、重开库持久化、ETF表共存及schema升级保护。CLI测试文件tests/radar/test_stock_research_cli.py；新增源文件在verify精确映射，避免默认全量。

实际验收从现有真实候选缓存及审核资料包导入独立tmp db，核对真实公司/证据数量、重复导入不增加记录、不自动新增成员。审核生命周期用明确标识的合成测试证据独立tmp db演示，不替用户批准实际股票证据。独立规格+质量双审查，范围Ruff/Pyright/pytest和git暂存检查后，本阶段源码／测试／正式文档合为本地提交，不推送／合并，不重复无关浏览器验收。

<a id="historical-2026-10-15-stock-radar-research-persistence-validation"></a>

### 股票共享候选与证据审核持久化验收

> 2026-11-24 文档整理：本节合并自 `2026-10-15-stock-radar-research-persistence-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期2026-10-15。对应实施计划 `2026-10-14-stock-radar-implementation.md#source-2026-10-15-stock-radar-research-persistence`。用户选择完整股票池并授权持续推进；本阶段把已有离线研究资料接入持久化共享候选／证据审核，不代表完整股票池交付。

#### 完成范围

新增StockResearchStore和四个radar CLI：stock-records-import、stock-records-list、stock-evidence-add、stock-evidence-review。stocks_专表保存公司、来源批次、不可变证据和追加审核；独立schema版本、不变更ETF表或PRAGMA user_version；WAL、5秒锁超时、BEGIN IMMEDIATE及公司revision CAS。导入整批原子、同批幂等、旧截点不覆盖新资料；缺名称补证保留已知名称，当前名称优先。

来源／字段变化产生新证据，旧审核不转移；资料自带financial_verified/status不具批准权。明确跨公司归属整批拒绝，空建议和失败文档不能确认。确认需完整来源、日期、摘要及显式审核人／理由；满90天过期，未来审核不生效。公司准入仍pending_verification，未添加成员发布入口或自动任务。CLI输入／输出保护db及其WAL／SHM／journal（含真实路径）。

#### 真实验收

从已有market-candidates.json导入685家公司。首次真实导入因302132中航成飞被旧前缀拒绝，整批无资料写入。随后从[深交所披露公告](https://disc.static.szse.cn/disc/disk03/finalpage/2025-04-29/e7ca3d43-4a5a-4c76-bb42-ad4f95dddb40.PDF)确认有效代码并修复校验，新增回归。这只确认身份，不代表符合准入。

最终新建独立tmp/stock-research-acceptance/final.db，经真实项目CLI导入685候选、三公司分红审核包、长江年报审核包，结果公司685、证据772、来源批次3、审核0；全体admission_status=pending_verification。未替用户确认实际股票事实。旧验收库重复相同候选及分红资料包，新增公司／证据均0、reused=true。全库搜索长江返回2家公司，600900详情含47条证据，JSON导出成功。数据和原始库都留忽略tmp，不提交。

StockPoolRepository检查红利价值池及成长趋势池items均为空；研究资料不是成员。合成证据审核、stale revision、90天和并发竞争由真实SQLite回归覆盖，不在实际股票研究库伪造批准记录。

#### 审查和检查

独立规格及质量审查复核通过；修复跨公司entry/origin/fields归属检查、空建议确认、大整数分页、连接PRAGMA初始化失败关闭等边界。最后名称保留缺陷由独立合成数据复现（新包仅代码曾将长江电力名称替换为600900），主会话局部修复并补两类导入／当前名三回归。

范围门禁命令 `.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff通过、Pyright 0 errors／0 warnings／0 informations，149个相关用例通过（29.32秒）。名称修复后仅复核受影响store/CLI，48个用例通过（2.57秒），源及测试Ruff、共享运行时Pyright通过；其余同状态104项复用，最终相关回归合计152项通过。`git diff --cached --check`通过。不运行全量、不重复无关浏览器验收。

先前检查异常已区分：新增CLI导入排版I001经Ruff修复；首次类型检查暴露可空get_company和故意异常JSON测试推断过窄，使用断言helper和诚实Any标注修复；真实302代码失败未发生部分写入。失败诊断留tmp；成功唯一测试目录按规范清理。无前端／SSE变更，无新运行依赖。

#### 后续边界

股票准入和风格／机会计算、全市场正式发现服务、成员审核发布、暂停／退出／恢复、任务及正式管理页面仍需接入。已确认数值与未确认数值继续区分，正式筛选保持关闭。个人逐条确认事实不替代确定性硬条件，不产生可入池结论。本阶段数据可供接下来的引擎与管理接口使用。


<a id="source-2026-10-16-stock-radar-index-scope"></a>

## 2026-10-16 指数成分初筛范围实施与验收

> 来源：`docs/superpowers/plans/2026-10/2026-10-16-stock-radar-index-scope.md`；整理前版本 `ad626dc`。


### 用户确认及边界

用户在2026-10-16讨论中同意按指数缩小首轮研究范围，并确认成长方向把创业板50替换为创业板指。红利价值池为中证A500（000510）、中证红利（000922）、中证红利低波动（H30269）的并集；成长趋势池为中证A500、创业板指（399006）、科创50（000688）的并集。按证券代码去重，保留全部指数来源。行业仍是池内分类，不新增行业池。

全市场5225家公司只是已采集的沪深普通A股身份底表，不代表通过初筛或有投资资格。指数范围也不代表通过持续盈利、现金质量、龙头与风险核验，更不代表当前出现交易机会。共同条件与两池风格条件继续分别计算。指数调出只改变发现范围，不自动暂停或退出正式成员。池外补充入口保留，不能自动把全市场当作指数失败的替代范围。

成长与趋势允许在同一池内分别标记，但本事项不启用未经确认的增长、估值或均线阈值。正式筛选与成员发布仍关闭，自动更新仍关闭。

### 探针事实与日期口径

2026-10-16实测五个官方公开文件均HTTP200。中证端点为`https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/cons/{代码}cons.xls`，四份为二进制XLS，行数分别500、100、50、50，日期字段均20261016。创业板指端点为`https://www.cnindex.com.cn/sample-detail/download-history?indexcode=399006`，为XLSX，100行，日期字段2026-09-30。

保留文件的snapshot_date与实际fetched_at，effective_date未披露就为null。不把抓取时间、文件日期推断成调整生效日，不把9月30日文件称为10月16日实时成员。历史研究日只接受该日实际抓取缓存，不联网下载当前名单冒充历史。部分来源失败时明确各池完整性，不回退全市场。

### 实施步骤

1. 纯解析与并集：严格校验指数身份、普通A股代码、名称、市场、单一日期、唯一性与预期样本数量，保留来源原字段；先编写边界测试。
2. 官方采集：复用当前请求预算，串行请求、来源间1秒、网络瞬时失败最多重试2次（3/10秒）、429停止该主机。请求流读取和解析各有15秒隔离上限；原文4MiB，XLSX展开20MiB/64条目；只使用已有pandas/Excel运行依赖。
3. 通用命令`radar stock-index-discover --as-of --cache-dir --output [--refresh]`导出两池范围与来源审计信息，不写公司审核或正式成员库。
4. 接入现有手动发现任务及页面时，按池展示范围并分清基础名录、初筛范围、筛选通过、正式成员；不得用全名录数量作为候选合格数。旧的web接通计划需按此规则调整后继续。
5. 独立规格与质量审查、相关检查、真实官方文件运行和同日缓存复用后，按完整事项本地提交。不把采集层完成表述为整个股票功能完成。

### 验收记录

原始文件探针去重结果为579/549家、总体628家。正式解析进一步发现：中证红利含北交所920599同力股份，科创50含689009九号公司CDR，均超出现有沪深普通A股范围。按已知身份记录scope_exclusions，保留原文字段；完整性按有效行加已知排除覆盖源样本数核验，未知身份仍不完整。创业板指302132中航成飞属于普通A股，不能因只识别300/301而错误排除。

落实上述范围后，真实复核红利价值范围578家、成长趋势范围548家，两池总体626家、共享500家；红利低波相对A500加中证红利额外覆盖13家。这只是来源快照的并集数量，不是符合盈利或风格条件的数量；创业板指快照比抓取日早16天，不能宣称是全体10月16日实时成分。

解析回归资源位于`tests/resources/radar/`：`csi-000922-constituents-20261016.xlsx`与`csi-000688-constituents-20261016.xlsx`各保留真实原表首条及末条（包括北交/CDR）；`cni-399006-constituents-20260930.xlsx`保留首两条及第93条302132。均从2026-10-16下载的原表经pandas读取、openpyxl重新封装为XLSX，保留字段和值，不宣称是原始响应字节。默认预期数量下裁切表应不完整；完整性测试显式指定裁切样本数量。真实完整原文与探针留在忽略的tmp目录，不提交缓存或整份下载响应。

已完成验证：解析测试31项通过、采集测试13项通过；父集成回归105项通过（指数CLI、更新runner/CLI、检查映射），最终资源与指数范围复核58项通过、65项不相关用例未运行；不是把重叠用例相加声称一次全量通过。最终12份相关Python文件Ruff通过；Pyright通过`tmp/index-integration-pyright.json`显式指向主仓venv和工作树extraPaths，0错误、0警告。

CLI注册与任务仓储额外冒烟首次69通过、1失败：上一阶段未完成网页接线引用不存在的api.stock_radar，使服务启动导入失败。撤下该提前接线并恢复原成员路由后，失败用例`tests/test_cli.py::test_run_builds_server_and_reports_ready_url`单独复验通过1项，其他69项结果复用。相应网页能力标记继续关闭，待后端/前端和真实浏览器验收完成后接通。未执行全量门禁或网页验收。

提交前`verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv -PlanOnly`确认无未映射文件且不触发全量；复用上述同代码状态检查，补充`tests/test_cli_index.py`与`tests/radar/test_collector_cli.py`11项通过，`git diff --cached --check`通过。范围计划中其他未修改的成员/名录测试复用前一完整事项1b9194a的相关验证，不因本次提交重复运行。

真实`radar stock-index-discover`采集并再次复用同日缓存，修正后产物`tmp/index-scope-acceptance/scope-verified.json`为complete，红利578、成长548、总体626，两个范围排除明示，创业板指源日期仍9月30日。首次真实运行因不识别302及未区分已知范围排除而partial，已保留原失败产物用于诊断，并加入真实样本回归。官方XLSX缺默认样式的openpyxl警告不影响名单解析。

真实任务往返：`radar stock-update-enqueue`与`stock-update-run-one`使用独立`tmp/index-scope-acceptance/research.db`、task-cache和artifacts。任务`2d0c3220d8dd48cb86aad8020e5c790c`完成index_scope_v1，新增626公司、626待核验线索；只读SQL复核审核0、正式成员0、成员事件0。同日再次运行`50196884e1114229b4ed7ec9a9bb3f17`五来源全部cached，新增公司0、线索0。原始摘要及fetched_at保持不变。产物中池范围标签、源快照日期和排除记录可追溯；公司资料存在不等于已获准入。


<a id="source-2026-10-16-stock-radar-management-web"></a>

## 2026-10-16 股票候选、审核与手动任务页面接通

> 来源：`docs/superpowers/plans/2026-10/2026-10-16-stock-radar-management-web.md`；整理前版本 `ad626dc`。


沿用用户已确认的池选择组件和分层管理设计，继续已授权实施，不重新设计侧栏、主题、页面坐标或数值门槛。使用现有CSS变量、panel、subs-btn、radar-note与紧凑列表。当前正式池仍为空，首次真实发布由用户选择且需规则启用；现阶段不能授予资格。

### 用户流程

股票池空态保留说明，准备候选按钮可用；点击在后台执行两股票池共享的指数成分范围发现，显示简短阶段和可展开逐源结果。2026-10-16用户确认红利价值池为A500/中证红利/红利低波并集，成长趋势池为A500/创业板指/科创50并集，详细口径见同目录index-scope计划。全市场名录只作基础索引，不能直接作为筛选候选。切换池或页面不取消任务，返回读取最新状态。管理入口按当前池读取初筛范围并标注未完成资格核验，每页15家，按名称或代码搜索；行业在未核验时显示待核验，不把交易所行业大类当申万两级。

点击公司进入详情，显示资料状态、共同条件与池条件分开核验的结论；来源、原文字段、审核历史按需折叠。已有可确认的原文事实可以保存confirmed/rejected/needs_evidence审核，必须有理由且带公司expected_revision，409提示重新读取。研究线索不能confirmed。未保存意见保留在页面会话草稿，关闭页面有未保存保护；返回保留搜索、页码和详情位置。准备任务不自动审核。

详情可保存当前单公司的不可变评估并保存关注草案；草案仍blocked，明确展示阻碍。正式新增/恢复按钮保持禁用并说明规则尚未配置；API写入也调用原成员门槛，不能只靠前端禁用。暂停/退出历史与草案分开查询。

### 后端契约

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

### 后台服务

新增仅处理手动queued任务的StockUpdateService，复用StockUpdateRunner。POST第一次使用才初始化并唤醒线程；应用lifespan在已有股票任务表含queued/running时恢复线程，空项目不因只读GET启动。退出时停止领取下一任务，当前任务继续心跳和租约直至完成；进程退出后的未完成任务按租约恢复，不提前释放给其他执行者。不会注册自动计划。服务循环使用Event.wait，runner保留15秒heartbeat/90秒token。成员行情窗口从已有radar_collector.db TradingCalendar缓存只读生成，禁止自动刷新或补工作日。

### 前端契约与验收

继续`renderRadarStockPool(root,header,pool,payload)`，可增加可选options(request,isCurrent)。stock视图自己管理每池搜索/页码/详情和未保存意见，用textContent渲染来源字段，拒绝脚本链接。异步请求及轮询只更新当前渲染，旧响应不能覆盖其他池；任务轮询每2秒、单链不重叠，离开后停止，返回查询恢复。主切换栏仍只有选择和更新；候选准备/管理在内容区。

pool_catalog把股票manage能力改true；update是否可点取决当前有成员和已实现任务，不因正式规则未配置而调用ETF。CSS只新增stock作用域，沿用共享坐标；更新相关CSS/JS入口缓存版本。语义DOM测试覆盖准备、搜索分页、错误与重试、409、保存/保留草稿、旧响应隔离，不能用静态字符串替代真实验收。

S级执行：后端服务/router与API测试代理，前端stock-view/CSS与行为测试代理，父路由接入/能力/缓存版本/兼容/真实验收；独立规格与质量双审及复审。真实服务从工作树显式venv及PYTHONPATH启动8765，完成一次真实会话收发、新功能准备任务及候选搜索/详情/审核拒绝/版本冲突、两ETF池兼容；宽屏与390px检查无溢出及截图，影响全局布局时按AGENTS逐页测量。已批准审核和正式成员选择不能由代理代填；浏览器真实写入只做准备资料与明确测试拒绝，合法审核用隔离合成API数据验证。

### 收尾验收（2026-10-17）

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


<a id="source-2026-10-16-stock-radar-membership-lifecycle"></a>

## 2026-10-16 股票池正式成员生命周期

> 来源：`docs/superpowers/plans/2026-10/2026-10-16-stock-radar-membership-lifecycle.md`；整理前版本 `ad626dc`。


用户已确认完整股票池持续实施。本事项实现阶段三的成员持久化与发布边界，不改变尚未确认的估值、股息率、趋势等数值规则，不发布任何真实股票成员。

### 目标与边界

新增独立 `StockMembershipStore`，复用 `StockResearchStore` 的SQLite连接、事务、公司版本和评估档案；不改ETF表、不使用YAML保存成员。研究候选、评估档案、草案和正式成员仍为不同层次。

正式成员只有 `active`、`suspended`、`exited` 三种当前状态。事件是追加式记录：`publish_add`、`suspend`、`exit`、`restore`。成员可同时属于两个池，各池独立；共同准入失效的后续任务可对两池分别提交暂停或退出，不能用价格变动强制退出另一个池。

每个池有单调递增revision。批量变更需给出每池预期revision；同事务内验证、写成员、写事件并递增池revision，任一项失败则整个批次不写入。相同request_id与完整相同内容幂等返回；同request_id不同内容冲突。理由、操作者和事件时间强制保留。查询默认每页15项，行业仅由已确认成员事实决定，未知行业不猜测。

### 发布规则

`add`、`restore` 必须引用 `StockEvaluationStore.assert_current` 的不可变评估，且指定公司仍保持相同source_revision；共同准入和相应池已批准条件都为passed，未配置规则为空，正式筛选显式开启。现阶段评估层固定关闭正式筛选，因此发布会明确拒绝，绝不因实现生命周期而放行。

`suspend` 只允许当前active成员；`exit`允许active或suspended成员；两者只记录用户审核后的理由，不以它们绕过新增门槛。`restore`仅允许exited或suspended成员，重新走当前评估门槛；旧评估、旧公司revision或过期证据不可恢复成员。已经active/suspended/exited的重复状态变更拒绝，不静默覆盖。退出成员保留历史，后续旧评估不能写回active。

将现有 `StockMembershipDraftStore` 保持为“关注草案”且publication_enabled=false，避免隐式把已存草案转换为成员。

### 接口、CLI与验收

新增 `radar.stocks.membership_store`，公开 `publish`、`get_member`、`list_members`、`list_events`；CLI新增 `stock-membership-apply` 与 `stock-membership-list`。apply输入只接受 `evaluation_id`（仅新增/恢复需要）和变更列表；不接受外部passed字段。CLI拒绝输出覆盖输入、数据库或池配置目录。

测试覆盖：空池、双池同股、原子批次、幂等与request冲突、CAS冲突、草案不升级、旧评估/资料revision拒绝、未配置规则与formal关闭拒绝、暂停/退出/恢复顺序、事件不可修改、分页及SQLite重启。新增文件映射到验证脚本。先运行相关测试、Ruff、Pyright；最后`verify.ps1 -Scope Changed -Source Staged`。真实验收只验证CLI拒绝当前不具备正式资格的候选并保证数据库未产生成员；不选取或发布真实标的。

### 实施记录

新增成员、池revision、事件与请求幂等四张独立stocks前缀表；草案表保持原样。第一次来源保留回归发现更新路径只读取成员revision，却尝试读取原评估来源字段，导致暂停/退出失败；已改为同次查询三个字段并增加来源保留断言。关联测试、Ruff与Pyright通过；最终暂存范围门禁另记。

最终暂存范围门禁：`.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`，Ruff通过、Pyright 0错误、pytest 264 passed（57.50秒），测试临时目录已清理。此前门禁首次运行的3项失败仅为验证映射预期未包含新增成员测试；更新映射断言后先行3项通过，再完成最终门禁。


<a id="source-2026-10-16-stock-radar-update-runs"></a>

## 2026-10-16 股票池手动更新与候选发现任务

> 来源：`docs/superpowers/plans/2026-10/2026-10-16-stock-radar-update-runs.md`；整理前版本 `ad626dc`。


用户已确认手动更新、候选发现分开，自动更新默认关闭。本事项新增独立任务持久化与CLI，不把股票任务接入ETF `UpdateService`，也不注册定时任务。

### 状态与幂等

`StockUpdateStore`使用研究SQLite的独立表保存任务：任务类型只能为`refresh_members`或`discover_candidates`，状态为queued/running/completed/partial/failed/cancelled，阶段为queued/snapshot/collect/review/finalizing及终态。任务保存池范围、目标日、规则版本、起始成员池revision、输入快照、逐项结果、错误与尝试次数。

相同类型、排序后的池范围、目标日、规则版本只允许一个queued/running任务；返回同一任务，不重复采集。新任务先捕获成员或候选范围快照，任务运行过程中成员版本变化不修改该输入；完成结果是不可变批次，失败或部分失败不会改写上一份完整资料。任务可由worker以租约claim，过期租约最多两次回队、第三次失败；更新只能由持有worker_id的执行者完成。

`refresh_members`必须有至少一个当前有效或暂停成员，读取正式成员仓储的快照；退出成员不进入更新。`discover_candidates`只记录准备范围和来源阶段，不把候选或名录直接升级成员。两种任务均不自动审核、发布、暂停、退出或恢复。当前正式筛选关闭也不妨碍发现候选资料，但结果始终`formal_screening_enabled=false`。

### 执行与资源

执行器直接复用官方名录、原始日线及股本采集器，不重新实现网络协议。候选发现执行官方名录→内容寻址产物→共享研究候选导入；complete仅代表名录资料发现已完成，不等于资格通过。成员更新去重证券后采集日线、股本，财务/分红/风险后台复核仍标not_configured，因此整项保持partial，不能冒充完整成员复核。回调异常逐来源隔离并留摘要；自动入口不注册，始终关闭。

任务仓储没有网络；执行器通过已有适配器联网，沿用已确认请求预算。整个股票队列同一时刻只认领一项，证券去重后串行采集。租约90秒、每15秒续租，每次claim生成新token；同worker旧token及过期租约均拒写。候选导入在同一SQLite写事务中再次assert_owner，避免旧执行者补写；只保存待核验research_hint，不自动审核。产物按SHA256归档且损坏旧文件不覆盖；缓存/产物目录不得相互包含、包含SQLite/配置或经过符号链接。默认分页15，单页上限100。

### 接口与验收

新增 `radar.stocks.update_runs`仓储与`update_runner`执行器。CLI提供`stock-update-enqueue`、`stock-update-list`、`stock-update-run-one`，仅用于手动任务；run-one支持缓存/产物路径及已验证市场窗口文件。结果scope的summary为文字，结构化统计和来源保存在artifact引用及原文产物；已完成scope幂等且不可覆盖，过期恢复续跑跳过成功scope，已终结批次不可变。

测试覆盖：双池规范化、空成员拒绝、资料不授予资格、并发幂等/单执行者、租约与旧token、过期续跑/成功scope不可改、事务内输入快照、部分失败、guard拒写、产物hash/损坏/路径、分页、CLI、自动关闭与验证映射。真实CLI执行一次名录候选发现，核对三源状态、研究库待核验资料与成员未发布；重复入队只复用活动任务，缓存复用不伪造采集日期。完成双审及Changed范围门禁后本地提交。

本轮另修复上一笔成员管理两项真实问题：请求重放先于日期新鲜度/禁用配置校验；暂停/退出结果不再误报formal_screening_enabled=true，并把首次评估新鲜度检查置于写事务。保持当前正式筛选关闭，不增加任何阈值或发布授权。

### 实施与真实验收

2026-10-16真实CLI：同请求两次入队都为aa243696efb844d88386efb4664b4fd5；三源均下载成功，原始行主板1702、科创618（排除CDR1）、深市2906，合并普通A股候选5225。首次执行追加5225条待核验线索、公司新增0，批次0869668e34f28386dd99d411c3fb20599a2917f0123c9ba3269af36dd30bcd00；正式筛选false、发布0。

第二次新批次执行全部来源cached，source SHA和fetched_at不变；没有新增候选证据。两正式池成员查询均total0/revision0。产物a34991e8e1fa629fce9053ad46d45cea9240d9881416f0ec6fd88cae8cf4b9aa与缓存产物均留tmp；缓存产物13185002字节，实际低于64MiB上限。SZSE下载文件的已知默认样式警告仅为openpyxl提示，没有隐藏或修改依赖。

集成首轮4项失败为summary接口不一致（执行器结构化对象、仓储要求文本），已改summary文字/统计置artifact，随后98项通过。补齐全来源失败与断点恢复后106项通过；最终新增旧partial续跑全失败按最新scope聚合、3/10秒退避回归将由暂存门禁覆盖。规格与质量独立双审及复审均PASS：全源失败不写空批次；成功scope恢复前重验产物；历史partial不能掩盖新失败。

最终范围验证：`verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`覆盖22个相关测试文件，Ruff通过、Pyright 0错误；pytest 511 passed/1 skipped/1 failed（105.40秒），跳过为Windows符号链接权限。唯一失败为既有财务CLI样本固定2026-10-15截点却使用当前2026-10-16时钟审核，系统正确拒绝历史时点采用未来审核。固定该测试文件所有实例的采集/审核时钟后，单文件10 passed（2.37秒）、Ruff及Pyright通过；其余已通过范围代码未变并复用结果，组合覆盖512项通过、1项跳过，未重复整轮门禁。失败检查目录保留用于追溯。


<a id="source-2026-10-17-stock-annual-batch"></a>

## 2026-10-17 指数范围年度财务批处理

> 来源：`docs/superpowers/plans/2026-10/2026-10-17-stock-annual-batch.md`；整理前版本 `ad626dc`。


继续已确认的数据采集与审核流程，不新增数值准入条件或页面。当前生产库626家公司只有研究线索；已有年报发现、归档、提取、审核包与规范事实准备可复用，缺少面向整个初筛范围的可恢复串联入口。

### 方案与探针

通用`radar stock-annual-collect`读取官方指数范围产物或显式公司列表，以证券代码去重；固定研究日和输入摘要绑定工作目录。按公司串联现有annual_report查询、官方PDF归档、前三个完整财政年度提取、build_review_packet。保留更正报告和未知标题线索，不静默挑一份金额。

2026-10-17官方查询探针：600900和000333均query_scope_complete，约5.4秒，发现5/4份年度文档。文档含fiscal_year、discovery_status及公开PDF URL。只归档最近三个完整年度相关文档；其他年份记录排除原因，不将查询完整当作报告/盈利核验完整。

逐公司结构化产物和SHA256检查点用于续跑；损坏完成产物拒绝复用，不静默覆盖。单公司网络或解析失败记录后保留其他成果；429停止对应来源并明确剩余公司未采集。沿用来源请求、原文大小及解析预算，不新增运行依赖；缓存1GiB软上限仅提醒，不自动删除。

可选显式`--db`将审核包导入既有公司库，并调用已存在的prepare_from_store/add_financial_fact_batch生成有来源依赖的待审年度财务组；保留原文和待核验状态、不增加审核。缺数据时不补造事实。默认只导出资料包。回调在检查点复用时也执行，恢复首次已保存但未导入的情形。输入、输出、工作目录和数据库家族严格隔离，不覆盖源文件。整体摘要展示已处理、可复用、失败、待处理及待审建议数，不展示“合格公司数”。

### 验证与交付边界

A类独立模块与测试，加CLI/有限集成与一次合并审查；复用现有采集适配器测试，不重复全量。覆盖输入验证、目录绑定、三年范围、跨公司拒绝、限流停源、坏摘要、断点复用与重复导入。真实官方运行保留研究产物到忽略tmp，源码、测试和计划按完整事项本地提交。

本事项仅年度财务资料，不替代最新季度/TTM、证券身份/上市年限、市值、金融专项、治理或龙头证据。尚未确认的风格阈值仍关闭；自动计划及正式发布关闭。批量采集可能需要较长时间，完成命令接通不等于所有公司已经采完。

### 实测记录与使用

2026-10-17以600900、000333的官方查询及完整PDF缓存进行真实批处理验收，覆盖2023—2025年：长江电力三份报告成功匹配，共12条年度财务建议；美的集团三份报告因格式未匹配而保留待核验。本次未扩大解析器支持：其公司代码在第8页、主要数据为逐指标千元单位，现有严格模板不能自动放行。再次运行复用两家公司断点。通过命令入口两次导入独立验收库后，2家公司、40条证据、0条审核记录，未发布成员；验收库和完整PDF留在忽略目录tmp。

完整和部分结果均复用，不自动覆盖待审产物；需要重采部分结果时使用新工作目录。429在本轮停止整个剩余批次，下一轮可继续尚未处理的公司；历史部分结果的限流标志不会永久阻断后续公司。

合并审查发现并修复原文断点完整性问题：首次归档与每次复用均核验成功PDF的URL绑定文件名、目录、文件头、真实长度及SHA256；原文缺失、截断或篡改时阻止回调导入。补充7个原文异常回归场景。相关范围153项测试通过，修复后仅复跑受影响的批处理与命令35项通过；改动Python文件Ruff、Pyright通过，未运行全量回归。

入口示例（PowerShell，输入与工作目录必须分离，路径按实际文件替换）：

```powershell
./.venv/Scripts/stock-robot.exe radar stock-annual-collect --input tmp/index-scope.json --workspace tmp/annual-batch-20261017 --as-of 2026-10-17
```

默认仅导出资料；显式添加`--db <已有数据库>`才导入既有公司并整理待审财务事实。该步骤不审核、不确认盈利、也不自动发布到股票池。摘要中的completed表示资料处理完整，不表示公司通过选股条件。

<a id="historical-2026-10-17-stock-annual-currency-definition"></a>

### 年报释义币种证据兼容

> 2026-11-24 文档整理：本节合并自 `2026-10-17-stock-annual-currency-definition.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

#### 依据与范围

继续已确认的财务资料采集与核验。真实626家公司采集任务在隐藏后台运行，输入为2026-10-16官方指数快照重新推导的范围，研究日2026-10-17，基于c4a79fb代码启动；只导入待审资料，不审核、不发布。本轮抽查已完成35家公司时全部为partial，主要原因是年度表不重复声明币种。

前20页内存在明确的报表单位释义，例如TCL科技第7页“元、万元、亿元 指 人民币元、人民币万元、人民币亿元”，第9页主要会计数据逐项标注元且明确勾选未重述。采用原文已有定义，不依赖交易所或行业默认币种，不增加依赖、页数或大小预算，不调整选股规则。无声明的美的集团仍待核验。

#### 实施计划与边界

1. 仅在有独立“释义”标题的页面识别明确单位→释义映射，按左右项目一一对应；缺项、对不齐、同单位定义冲突不放行。仅使用数据表所在页及以前的释义，仍限前20页。
2. 本表有币种声明时优先核验本表，不允许报告通用人民币定义覆盖本表外币或冲突声明；本表没有声明时，每个指标的原始单位都必须有一致的人民币释义。保留释义页码、原句和含义到currency_evidence，审核包不得丢失。
3. 接受明确标准Unicode勾选☑和√，必须绑定完整重述问题；不猜测PDF私用字体字符，不处理列折断或重述比较表。
4. 真实样本、拒绝用例、审核包追踪测试后进行一次A级合并规格与质量审查。运行相关财务、季度兼容、审核包及批处理测试，单文件Ruff/Pyright；不重复全量。
5. 修复只影响新进程。现有采集进程仍按原版保存原文与断点；真实复核通过后可用现有提取/审核包/事实准备函数离线重处理已归档报告，输出新研究产物，保持原任务断点不变，不重复抓取、不自动确认。

#### 真实样本

`tests/resources/radar/cninfo-000100-2025-annual-data.txt`来自官方全文`https://static.cninfo.com.cn/finalpage/2026-03-28/1225045623.PDF`，披露日2026-03-28，2026-10-17由现有采集流程归档。裁切PDF第1、2、7、8、9页pypdf文本，空页和分页符保留实际页码，仅去掉行尾空格；完整PDF留在忽略tmp。样本无需合成币种声明即可验证2025年收入184063390556元、归母4516783411元、扣非2896878481元、经营现金流44021698580元。比较年度仍为待审建议。

单个模板匹配不表示财务事实已审核，资料处理完整不表示股票合格，仍需后续最新TTM、风险、龙头等证据及已确认的选股条件。

#### 验收记录

相关解析、季度兼容、审核包、年度批处理、规范财务事实及验证入口303项测试通过。合并审查发现本表币种值前缀匹配会误接收“人民币、美元”等混合声明，已改为按声明边界完整匹配，补3个混合/折算声明拒绝用例；仅复跑受影响解析和审核包80项通过。Ruff、改动文件Pyright通过，资源验证映射无遗漏。

离线重处理TCL科技3份官方年报：两份匹配、一份重述状态不明确；限制2023—2025窗口后20条建议，原文及新提取/审核包保存在`tmp/annual-currency-reparse-20261017/000100/`。导入已有真实库新增24条待审资料，并准备2个规范数据组（盈利与现金流）；公司证据39条均为pending_verification，审核0条、publication_enabled=false，真实公司详情接口HTTP200、可读20条财务建议。未使用此次改动重启或覆盖原626公司采集任务；其进度与此次离线重处理统计分开。


<a id="source-2026-10-17-stock-annual-offline-reparse"></a>

## 2026-10-17 年报任务批量离线重解析

> 来源：`docs/superpowers/plans/2026-10/2026-10-17-stock-annual-offline-reparse.md`；整理前版本 `ad626dc`。


### 范围与授权

延续已确认的626家公司财务采集与待审流程。用户明确要求没有待确认事项就一直推进。本事项为已归档报告补通用重解析入口，不新增选股条件、网络来源、调度开关或页面。现有后台采集基于c4a79fb运行，已归档资料不应因后续解析器修复而反复下载或被旧partial断点永久锁住。

### 行为与接口

新增`radar stock-annual-reparse --source-workspace <原任务目录> --workspace <新研究目录> --as-of <同一研究日> [--db <已有库>]`。只读原任务manifest、逐公司result/checkpoint和PDF。调用现有归档核验、extract_archived_financials、build_review_packet和待审事实准备；不调用公告发现或下载。默认导出，显式db才导入已有公司且仍待审，禁止审核或发布。

原manifest版本为stock-annual-batch-v1，验证研究日、非空去重公司清单和input_sha256。目标清单绑定原manifest原始摘要、源绝对路径、研究日、重解析版本和financial_extract.py内容SHA256；解析器变化须使用新目标目录。源/目标互不嵌套、拒绝符号链接；数据库及日志不能位于源/目标目录内。复用原annual_batch的原子写、公司代码校验、摘要及PDF核验机制。

每次调用只处理已存在完整checkpoint的公司；原任务仍在进行而无checkpoint者为pending，不读写其未提交结果。读取result并验证checkpoint摘要、公司/名称和源清单归属及PDF绑定。损坏的源或目标记录拒绝提取/导入并报告，单公司故障不阻断其他公司；原目录保持不变。将提取建议限制在此前三个完整财政年度，所有currency_evidence等原文追踪保留。

结果保存到目标companies/<symbol>/result.json和checkpoint.json。复用时同时核验目标摘要、源result摘要及现存PDF，回调仍执行以恢复已提取但未导入的资料。回调失败保存进度后抛出可重试错误，不静默降级为成功。目标结果完整或部分均复用；不能自动覆盖既有待审资料。后续新增源checkpoint可在同一目标目录继续处理。

摘要区分processed、pending、completed、partial、failed、reused、companies_with_proposals、financial_proposals、import_failed及原始报告matched/pending数量，强制financial_verified=false、formal_screening_enabled=false、members=0。completed仅表示资料匹配完整，不表示选股合格。原查询范围、三年度缺失、歧义文档和失败归档仍导致partial，不因重解析而抹除。

### 实施与验证

1. A级新模块annual_reparse.py与独立测试，复用已有工具函数。覆盖源/目标绑定、活跃源pending、增量续跑、无网络、原/目标损坏、丢失或篡改PDF、跨公司拒绝、三年范围、解析器指纹变化、回调恢复与失败。
2. CLI复用年度采集的待审包导入工厂，保持原命令行为。补CLI注册、路径隔离、研究日及重复导入测试，在verify.ps1映射相关源码。
3. 一次合并规格/质量审查，运行相关测试与Ruff/Pyright，真实源目录离线批量导出及导入验证，不重复全量，也不为CLI数据改动触发UI布局或聊天验收。
4. 验证稳定后按完整事项本地提交。运行产物、PDF、数据库、日志留在忽略tmp。本轮可先处理现有已提交源资料，原采集结束后用同一命令继续剩余部分；不引入周期自动任务。

### 实测与检查

新增核心19项、CLI及原命令14项独立测试通过。相关解析/审核包/财务事实/CLI/验证映射共229项通过，另1项旧年度采集测试在os.replace摘要时遭遇WinError5；完整异常保留在`tmp/pytest/annual-reparse-related-38360-ce38f87b34834ccd894ad6e5da1624cd/`，文件仅Archive/Compressed属性，无ReadOnly，新唯一目录单独复核该用例通过。由此给共用_save补Windows5/32/33最多两次50/200ms短重试；非Windows权限错误和永久拒绝仍抛出、不改ACL、不改变旧目标，新增3项回归，受影响年度采集和重解析51项通过。所有改动Python文件Ruff、Pyright通过。未运行全量；合并规格/质量审查通过，存储小修由主会话diff和相关测试核对。

依赖映射补充后，检查脚本77项通过，3个精确集合断言需同步新增重解析测试目标；更新断言后仅复核该3项通过，没有重跑整套业务测试。失败记录保留在独立tmp/pytest目录，未清理首个存储异常产物。

真实调用（PowerShell，项目虚拟环境）：

```powershell
./.venv/Scripts/stock-robot.exe radar stock-annual-reparse --source-workspace tmp/annual-collect-live-20261017-c4a79fb --workspace tmp/annual-reparse-live-20261017 --as-of 2026-10-17 --db .stock_robot/radar.db
```

2026-10-17已启动有限后台流程：先对当前归档离线运行一次，等待原采集进程（同时核对PID与启动时间，避免等待复用PID）结束，再对同一目录续跑一次。脚本及状态位于忽略的`tmp/jobs/annual-reparse-follow-20261017.*`，非周期任务，自动更新设置仍关闭。初始实际处理98家公司时8家有108条待审金额建议，重解析及导入失败0；源采集同时推进，数据未审核、成员未发布。原文缓存实测2.36GiB时已向用户说明超过1GiB软提醒值，按已确认规则保留、不自动删除；新流程没有复制PDF。

#### 公告请求预算复核

2026-10-18发现公告发现器此前将两次暂态重试都按普通请求间隔1秒等待。按已确认预算修正为3秒、10秒；正常请求间隔仍为1秒，最多重试2次、连接及读取超时各15秒、429立即停止来源。新增HTTP503及网络超时两组断言，验证实际请求次数、超时参数和等待序列。公告模块47项测试通过，相关两文件Ruff和Pyright均通过；不涉及UI、审核或发布。首次新增测试误将旧测试尾部断言放入新函数，调整测试边界后复核通过，失败产物留在tmp/pytest便于追溯。


<a id="source-2026-10-17-stock-szse-annual-format"></a>

## 2026-10-17 深市年报文本格式兼容

> 来源：`docs/superpowers/plans/2026-10/2026-10-17-stock-szse-annual-format.md`；整理前版本 `ad626dc`。


### 范围与依据

继续已确认的年度财务采集流程，依据用户此前“不用我确认，一直往下做”的授权实施格式兼容，不修改盈利或风格准入标准、不增加依赖、不增加前20页和25MiB解析预算，不审核或发布成员。原解析器要求前两页公司代码和整表人民币单位，美的集团三份全文的公司代码在第8—9页、主要财务数据逐项标注千元，因此停留在公司身份缺失。

方案：保留前两页年度标题要求，从前20页明确的“一、公司信息”章节补取代码，冲突拒绝；识别“六、主要会计数据和财务指标”和连续年度列。逐项单位必须齐备、无重复歧义，本表表头必须单独明确声明人民币；通过明确勾选“否”识别未重述，其他状态待核验。只在这些条件均成立时沿用原建议格式和Decimal换算；仍为待审建议。真实原文未声明本表币种，不借用分红段落或其他表格中的人民币，保留待核验原因及已归档原文。

### 样本与探针

2026-10-17读取此前已归档的000333官方完整年报，不新增网络请求：2025年度来源`https://static.cninfo.com.cn/finalpage/2026-03-31/1225065145.PDF`，披露日2026-03-31；2024年度来源`https://static.cninfo.com.cn/finalpage/2025-03-29/1222951181.PDF`，2023年度来源`https://static.cninfo.com.cn/finalpage/2024-03-28/1219429374.PDF`。前20页均未发现能够关联主要数据表的币种声明，2025年的分红段落出现人民币，不能代用。

资源`tests/resources/radar/cninfo-000333-2025-annual-data.txt`为2025报告第1、2、8、9页pypdf文本裁切，仅移除行尾空格，其他位置保留空页及分页符，保持实际页码。完整PDF和其他年度文本不提交。测试中的明确人民币声明是显式合成变体，不能反写到真实样本或生产资料。断言2025年收入456451731千元、归母43945411千元、扣非41267233千元、经营现金流53345930千元，仅在合成明确币种表中验证人民币元换算。

### 实施与验证

1. 原解析器内补公司信息身份和深市年度表格式，保持原沪市分支。
2. 真实样本应解析到币种待核验，不应产生规范金额建议；合成明确币种样本生成12条三年建议，仍需审核。
3. 覆盖币种缺失/冲突/外币、别处人民币、代码冲突、逐项单位缺失、季度表、重述选择不明；执行相关财务、审核包、年度批处理测试和单文件Ruff/Pyright。
4. A级一次合并规格/质量审查；将真实资源映射到verify.ps1，按完整事项本地提交。真实批处理使用新工作目录，避免覆盖已保留的partial结果。

### 限制

本事项不声称美的集团已完成财务核验。后续需要有来源的明确币种证据，再准备规范化财务建议；当前始终不自行补为CNY，也不改变持续盈利或现金流规则。

### 验收记录

六份官方年报缓存复测：600900三份仍各有4条财务建议，000333三份均能定位年度表，因币种未明确而保留待核验、0条金额建议。2024报告封面实际为“2024年度报告”，已兼容可选重复“年”，仍要求前两页单一完整年度标题。真实原文未添加币种信息。

相关财务提取、季度兼容、审核包、批处理和验证入口测试257项通过；随后标题兼容及审查修复仅复跑受影响解析测试，最终46项通过。审查提出重述勾选应绑定对应问题，已收紧为完整重述问题紧跟明确“否”，并补替换问题文本但保留“否”的拒绝测试。Ruff、改动文件Pyright均通过，资源verify映射无未覆盖文件。未运行全量回归，无UI、数据库或成员发布改动。


<a id="source-2026-10-18-stock-structured-financial-implementation"></a>

## 2026-10-18 股票池结构化财务接入实施计划与执行边界

> 来源：`docs/superpowers/plans/2026-10/2026-10-18-stock-structured-financial-implementation.md`；整理前版本 `ad626dc`。


### 确认依据

用户在了解两条路线的能力与限制后明确表示：“就选当前的这个新方案继续处理”。本计划执行2026-10-18《股票池结构化财务数据路线核查与调整方案》，不将“继续推进”解释为允许自行更换路线。用户同时要求避免再次未经讨论上手，本计划将执行边界与验收记录持久保存。

### 执行边界单

- 主路线：已实测的AkShare/同源东方财富结构化利润表和现金流表，明确归母、扣非归母、现金流总额；保存结构化响应与版本。主要指标仅辅助交叉检查，不反推现金流。
- 初始范围：既有指数成分初筛的626家公司，使用输入清单与摘要绑定，不扩为全A候选。代码应支持未来显式提供的新范围，不把626写成业务永久上限。
- 规则：保留用户已确认门槛及红利/成长差别；金融企业独立处理。缺失、未知或会改变筛选结论的冲突为待补，不当作不合格；初筛不是正式入池。
- 禁止事项：不启动公告PDF发现/下载/解析/重解析，不添加失败时自动抓PDF的分支；不新增每家公司人工读财报的前置条件；不删除旧文件、清空研究库或将旧PDF建议混作结构化新结果；不自动审核、发布或启用周期任务。
- 预算：复用已确认并发最多2、15秒超时、最多2次重试与3/10秒退避、429停止来源、1GiB软提醒不自动删。优先请求必要报告期，复用类型/报告期缓存；不默认重复获取全历史。
- 实施位置：新独立工作树stock-structured-data，基于4d9b0e2。旧stock-radar-pools工作树的季度PDF实现及其未提交修改原样冻结；新事项不把那些修改混入提交。
- 需再确认的变化：更换主要数据来源或采集路线、扩大候选范围、增加人工研究/审核依赖、新增运行依赖或显著超出已说明资源规模、改变业务门槛及验收口径。先说明变化、替代方案和成本，再等明确回复；不能仅以“更可靠”为由自行扩展。
- 不需重复确认：本方案内的字段适配、缓存/错误处理、相关测试、明确小修复、本地阶段提交。进度必须报告真实覆盖与缺口，不能用脚本/流程完工代替数据和功能验收。

本边界单是本任务的操作记录，不擅自修改用户全局规则或AGENTS.md。正式采集器将仅允许固定结构化端点/元数据页，不提供任意下载URL或PDF回退；测试覆盖拒绝PDF与未知来源，避免仅靠文字约定。

### 阶段及完成条件

1. **数据契约与兼容修复。** 固定来源字段、单位、归属/合并范围、累计期间、披露/更新时间与抓取时间；隔离现有PDF记录。先修复已知纯TTM诊断顺序依赖：相同证据不同读取顺序应生成相同规范事实，保留真实失败样本回归，不采集新资料。
2. **结构化采集与小范围验收。** 在现有数据层、缓存和任务边界内接入必要字段，不另建采集框架；对4家公司复核元数据、空值/冲突、目标期、429/超时/缓存恢复。禁止任何公告回退。原始响应及其摘要可追溯。
3. **626家初筛。** 用户现已授权；在小范围与相关检查通过后执行，输出各条件通过/淘汰/数据不足、来源失败与未知金融适用性。记录当前采集日期，不伪装为历史时点数据；不得把财务初筛通过等同龙头/治理/全部风格条件通过。
4. **接现有手动更新及验收。** 复用现有池、行业、轮换/变更记录；必要时才改页面数据接入，沿用既有视觉。前端/会话有实际改动时按AGENTS真实服务及浏览器验收；纯数据/CLI阶段不重复UI或LLM调用。

阶段具有独立可审查业务价值时本地提交，源码、测试及相关文档成套提交；不推送/合并/发布。按AGENTS分级审查和相关范围验证，不默认全量。

### 真实兼容样本说明

此前8家季度PDF任务在金山办公688111保存规范事实时停止。只读复现已确认：按入库rowid与按evidence_id读取同一组资料，唯一差异在`ttm_summary.unresolved`诊断列表的排列；存储层精确重建因此拒绝保存。该缺陷属于纯计算结果确定性，结构化接入同样需要正确处理。

既有隔离诊断样本`stock-radar-pools/stock_robot/tmp/quarter-collect-sample-20261018/688111-evidence-order-repro.json`包含39条原始证据对象，不再取公告或读PDF。回归资源仅裁取其中两条被TTM拒绝的年度记录、当前期间归母及扣非季度记录，保留原始字段与身份，不伪造理想化数据。测试资源按AGENTS放在`tests/resources/radar/`，采样发生2026-10-18，参考截点2026-10-17；只验证确定性与旧记录兼容，不以此继续PDF路线。

### 阶段1兼容修复验证

先以真实裁切记录复现：按记录顺序准备与按证据ID准备不一致，真实SQLite往返同样抛出规范事实重建冲突。首轮终端异常输出被截断，随后仅重放2个失败用例，将完整异常保存在`tmp/pytest/ttm-order-trace-25772-d4d55ac7539345b98111992e75889f14/failures.log`；未将被截断输出表述为通过。

仅在纯prepare_ttm入口将相关证据按ID排序，保证诊断、金额引用及事务重建采用同一顺序；不修改金额计算、规则、审核或任何数据来源。新增三项回归（两个读取排列、一项真实SQLite往返），无审核。主会话按B级小修核对diff，未派代理。

验证命令使用主项目虚拟环境及本次唯一tmp目录：`python -m pytest tests/radar/test_stock_interim_ttm.py tests/radar/test_stock_interim_ttm_integration.py tests/radar/test_stock_financial_fact_store.py tests/radar/test_stock_financial_facts_cli.py -q`，84项通过；资源映射定向1项通过。改动Python文件Ruff通过、Pyright 0错误0警告。未运行全量，未重复浏览器/LLM验收。阶段1完成了已知兼容修复及边界记录；结构化采集接入与626家初筛尚未运行，不能称整体完成。

### 阶段2数据契约与入口

新增薄适配器`structured_collect.py`，以已实测AkShare 1.18.81同源固定端点获取原始JSON和公司类型元数据页。生产HTTP使用既有`bounded_provider_call`控制可取消的15秒调用，不修改全局SDK，不新建通用采集框架。结构化源失败只保留错误及已取数据，没有公告发现或PDF回退；没有业务库写入、自动审核和成员发布。

`structured_financial.py`输出独立`eastmoney_structured`产物，而非伪造`matched_template`、页码、原文摘录或审核身份。每个金额引用保留源表、字段、原值、元单位与口径、累计期间、报告/披露/更新时间、响应SHA256、抓取时间和原始响应位置。归母和扣非使用利润表主源，现金流使用`NETCASH_OPERATE`总额；营业收入`OPERATE_INCOME`不与主要指标营业总收入混合。币种非CNY、公司不匹配、日期或金额无效、重复版本金额冲突均不能输出可用金额。更新日超过研究截点的记录保留明确标记；所有结果均标记为当前采集，`point_in_time_verified=false`，不能回溯宣称历史时点已知。

三年年度窗口沿用现有规则的`as_of.year-3..as_of.year-1`。TTM固定上一年度＋最新累计－上年同期；最新期间缺失不回退旧季度或以年报顶替。主要指标交叉差异逐条保存主/辅金额和引用，后续具体筛选条件必须评估差异是否改变结论，不自动解释为舍入误差。三年现金流/归母之比只提供计算值，适用性等待明确发行人分类，不把银行套进普通企业门槛。本阶段数据完整不等于任何风格条件通过。

入口：`python -m stock_robot.cli radar stock-financial-collect --input <公司清单JSON> --workspace <独立目录> --as-of YYYY-MM-DD`。输入可为公司对象列表或带`companies`的对象，工作目录与公司清单摘要、原文件摘要、日期和版本绑定；每家公司立即保存不可变产物及进度摘要。重复执行复用源响应缓存，可显式`--refresh`。单公司失败为数据缺口并继续，429停止同来源后续公司。输入不得放在输出工作目录中，没有`--db`或隐式沿用旧PDF研究库的选项。

#### 离线样本来源和测试范围

`tests/resources/radar/eastmoney-structured-financial-samples.json`来自2026-10-18既有20次结构化探针响应：000100、600900、300750、600036，各2023/2024/2025年末、2025/2026半年报。仅裁切公司身份、期间、披露/更新时间、币种及本方案明确的金额字段，保留原始数值与源响应摘要；完整JSON/HTML响应继续留在忽略的`tmp/structured-data-audit-20261018-9f8bbcef/`，本次不重复获取原始公告。测试资源内来源摘要指向完整响应，不把裁切JSON自身哈希冒充源响应哈希。

先新增回归后实施：财务适配和CLI分别因模块尚不存在而出现预期收集错误，完整日志保留于唯一`tmp/pytest/structured-red-*`和`structured-cli-red-*`。现有定向测试验证真实20期间、精确十进制TTM、归母主源差异、缺失/非有限/布尔金额、公司身份、银行收入字段、重复版本、不降级最新期、清单绑定、429停止、错误隔离和旧视觉/业务库不受此入口影响。新资源及源码已映射`verify.ps1`相关测试，真实网络验收结果及最终检查另记于下文。

#### 阶段2验收记录

- A级合并spec/quality独立审查通过。修复刷新覆盖旧响应的P1问题，改为`content_sha256.raw`保留各版本，当前请求索引独立更新；并修复Python 3.11没有`Path.is_junction`时的兼容，使用`lstat().st_reparse_tag`后备检测，CLI复用相同路径保护。
- `./scripts/verify.ps1 -Scope Changed -VenvPath D:/code/stock_robot/.venv`通过：Ruff通过、Pyright零错误零警告、相关200项测试通过（82.13秒），覆盖新三组测试、CLI入口及验证映射；未全量。完整门禁日志在`tmp/pytest/structured-gate-41528-06bdff984d6d41d1a1f4945c6f39f286/verify.log`。
- 新CLI真实执行`tmp/structured-live-four-input.json`四家公司，研究截点2026-10-18，工作目录`tmp/structured-live-four-20261018/`。4家均`data_complete`，最新TTM期间2026-06-30；主/辅源金额差异仍为TCL两条、宁德两条，其余零条，全部保留。20份HTTP请求响应的真实SHA256及长度逐一核验通过。二次同CLI运行后20份请求索引、抓取时间和摘要完全未变，验证缓存复用；没有刷新或扩大来源。
- 一次边界红测试漏注入网络替身，错误进入生产请求路径，实际在测试临时目录保存11份结构化响应，1,845,070字节：非法代码888888/123456/510300各1份，000100配未来截点5份、过早截点3份；另3次类型页尝试未保存状态，不宣称未发出或失败。无业务库写入。已修复入口校验，并用autouse fixture使默认生产请求入口在离线测试中立即失败。保留原失败目录及逐项URL/参数/SHA/长度审计`tmp/pytest/structured-boundaries-red-32320-52aed385-e725-4e73-90c5-13dbfc376bbb/network-incident-audit.json`，不将该次运行称作离线通过。
- 阶段2完成统一财务采集与字段适配的小样本验收。尚未运行626家采集/财务初筛，未接手动更新，未验证正式入池；没有前端改动，未重复浏览器或LLM调用。数据完整数不能表述为合格公司数。

### 阶段3数值初筛与范围采集

阶段2本地提交`e02dbcc`，随后复核原指数范围完整产物`index-scope-acceptance/scope-verified.json`：2026-10-16快照、`scope_complete=true`、626家公司，原始文件SHA256为`83e3ba27530991da93ab20c5eb0f6251bc38ab83fae8ea46800036faaef2e93b`。保留完整原文件为新工作树`tmp/structured-scope-626-input.json`，明确其成分日期与2026-10-18财务抓取日期不同，不刷新或扩大候选范围。采集工作目录`tmp/structured-scope-626-20261018/`；串行执行，各公司产物立即保存，429停止同来源。该任务仅获取结构化财务，不改旧研究库或成员。

`structured_screen.py`复用既有严格正盈利门槛：最近固定三完整年度归母/扣非各一项，加最新季度TTM归母/扣非，共8项；逐项区分`passed/failed/pending_verification`，已知失败优先但保留其他缺口。主要指标与利润表有差异时，检查各年度及三期TTM替代组合是否改变严格大于零判断；会改变的为待核验，不选更有利数据。

现金流0.8只输出数值参考，精确交叉相乘，成长池仍仅质量提示。发行人身份与适用性未核验，不据此淘汰金融或未知公司。初筛不替代市值、上市年限、流动性、行业龙头、审计治理、分红、估值及成长趋势等完整规则，也不开放正式筛选/审核/发布。没有新增逐公司人工读财报的前置条件。

新增离线入口：`python -m stock_robot.cli radar stock-financial-screen --input <采集工作目录/summary.json> --output <目录外结果JSON>`。读取原子摘要快照并校验绑定清单、版本、公司身份、产物路径、实际长度和SHA，从`collection`重新规范化及计算，不信任历史`normalized`字段或调用方附加的结论。未尝试公司单列；产物缺失/损坏是待补，不能计为淘汰。输出禁止覆盖采集工作目录，可在采集过程中只读已完成部分，不新增网络请求。

#### 阶段3验证与审查

- 先红复现离线入口尚不存在，再实现；初筛核心真实4家公司及数值/跨源组合边界23项通过。发现合法数值域内`1 + 1e-1000 - 1`被固定100位精度变为零，先红复现后复用既有`_sum_amounts`精确求和；过长定点金额用科学记数保留，不扩大或收紧金额门槛。完整异常在`tmp/pytest/structured-precision-red-4688-11788fb3f17f4fa0a33f41bdbcd46d03/result.log`。
- 本阶段Changed门禁Ruff/Pyright通过，相关231项测试通过（61.08秒）；日志`tmp/pytest/structured-screen-gate-40380-3478557a54d945aa97398caa6a47efcc/verify.log`。没有全量回归。
- A级合并审查发现1个P1：辅助主要指标有相反金额的重复修订时，辅助单元格为`conflict`后被整个跳过，导致错误放行。已按审查建议保留每个有效金额及对应来源，纳入年度/TTM替代组合；两项回归先红2失败，修复后受影响的规范化、初筛核心和离线入口共51项通过，相关Ruff/Pyright通过。原审查不是无问题PASS；小修少于50行按AGENTS主会话diff和相关测试收敛，没有重复派复审。失败日志`tmp/pytest/structured-revision-red-7016-3159ea3b9ad34af9adfdc758ee8d45b0/result.log`，修复验证日志`tmp/pytest/structured-screen-fix-17924-ce02ab52eaf54b8ba6b744ddb3f70607/result.log`。
- 新离线CLI真实读取阶段2四家产物，4家八项数值通过；另对当时626任务的部分快照执行：已尝试138家、数值通过105家、不满足33家、待核验0家、未尝试488家。该计数仅是验收时快照，不能作为626总体通过率或正式候选数量；当前及最终结果以工作目录摘要和离线报告为准。

阶段3通用初筛实现与小样本贯通已验证；626范围采集仍在运行，未完成全范围覆盖统计，也未接手动更新及正式入池验收。阶段4不因数值初筛通过而自动启动审核、发布或周期任务。

### 续跑与阶段4设计边界

2026-10-18用户继续任务时，原采集进程已不存在，摘要停在496家：383家资料完整、113家不完整、130家未尝试；离线复算为盈利数值通过307、不满足77、待核验112。资料完整与盈利判断覆盖不同，不能混用计数。113个来源缺口集中在清单第272至384项，其后112家已有恢复成功，故不能把旧日志中的10053/10054认作当前仍连续失败。

仅对首个缺口300972的既定结构化主要指标端点做一次15秒边界探针，返回HTTP200、150309字节；此前PowerShell引号错误的命令为语法失败，没有发出HTTP。随后在同一输入清单、同一工作目录恢复采集，复用已校验缓存，采集命令结束后直接串行执行离线初筛，无另一个轮询等待进程。续跑过程中只读核验当时2914份请求缓存的实际长度/SHA256全部通过，缓存502.4MiB，未触及1GiB软上限。

财务任务的时间成本已成为新的设计依据，用户明确选择独立手动财务任务，保留行情更新原流程。具体设计见[独立手动财务复核设计](../../specs/2026-10/2026-10-13-stock-radar-design.md#source-2026-10-18-stock-structured-data-route)；可视预览已实际验证并呈现，当前仅方向确认，待用户确认入口、状态、结果及沿用现有串行队列的具体预览后再正式实现。此等待不停止已授权的626家采集和离线复算。

续跑及离线复算最终完成：626家全部已尝试、资料完整626、资料不足0；8项盈利数值条件通过492、不满足134、待核验0、未尝试0。统计来自同一固定范围的最终采集摘要和`tmp/structured-scope-626-financial-screen.json`，不能表述为正式入池人数；金融适用性及其他资格、风格条件尚未覆盖。

用户对首版卡片预览反馈“最好是沿用之前的消息小弹窗，拆分处理”。已修订隔离预览为共用可收起消息窗口，行情与财务各自入口及状态，财务结果单独弹窗查看；保留串行队列建议，未将拆分解释为并行采集。浏览器验证任务切换、行情排队、收起、结果弹窗及390px无水平溢出，仍待修订预览确认；未修改正式业务代码。


### 手动任务网页接入实施


依据：已确认的独立财务任务设计与消息小弹窗预览；用户追加要求直接复用原组件拖拽。沿用隔离工作树，不触发PDF采集、自动审核或正式成员发布。

#### 1. 后台任务与只读结果（跨文件实现）

- 测试先行：独立任务类型financial_review；从当前池最新完整指数范围冻结清单/来源摘要，拒绝空或不完整范围；不使用搜索结果、全市场或成员清单替代。
- 复用StockUpdateStore全局串行队列、去重、租约心跳。通过显式snapshot入口传入已验证范围；恢复保持原输入。
- StockUpdateRunner逐公司调用既有结构化采集和初筛，逐项落不可变产物；恢复校验SHA/长度/公司/日期，429停止来源，网络失败记待补而非不合格。缓存沿用结构化来源缓存，不取公告。
- API新增范围GET、手动启动POST、按kind恢复最新任务、结果分页GET；公开摘要/条件/来源日期，不暴露本地路径、worker/租约等私有字段。
- 不改审核、成员、周期任务；行情仍只更新行情与股本。

#### 2. 共用消息组件与股票页面

- 从radar-update-ui.js原样提取消息/结果窗口外壳及拖拽函数，原ETF组件改为调用外壳；不保留第二份拖拽实现。
- 股票新增消息适配器：按pool+kind保存状态与独立轮询；行情、财务各入口，候选准备作为独立任务保留。轮询不依赖页面DOM；切池恢复各任务，迟到响应不覆盖当前选择。
- 财务启动先读取固定范围数量并确认，执行中显示进度，结束后按15家公司分页查看结果与8项条件；缺失/不满足明确区分。
- 股票页移除原常驻任务卡片，原池切换器、候选/审核页面保持；ETF原行为与海外动态保持。更新静态资源版本。

#### 3. 验证与审查

- 定向pytest（唯一tmp目录）与Ruff/Pyright；不自动全量回归。
- 项目S级要求独立规格审查及质量审查，修复后复核。
- 使用真实stock-robot run路径启动隔离验收服务，浏览器验证拖拽（含收起后）、键盘复位、任务切换、结果、ETF回归、390px无溢出；完成一次真实发送与响应验证，记录所用数据范围和外部请求。
- 提交完整源码、测试与正式文档，预览/缓存/截图不提交，仅本地提交。


#### 2026-10-22续作状态

2026-10-18实现因账户额度及自动审批服务不可用而中断，不能视为完成。已保留源码及定向测试：共用窗口、ETF更新、股票任务、管理页与池切换20项通过；规格审查发现跨池状态、来源展示、未处理标签、完成刷新及迟到错误等边界并已修改，复审及后台最终门禁尚待完成。真实CLI启动通过；浏览器发送“你好”收到缺少llm.api_key的可见响应，未调用外部模型，不将其称为有效模型生成。尚未完成真实财务结果和鼠标拖拽验收，尚未提交。本次恢复不重新采集626家公司。


#### 网页接入验收与收敛（2026-10-23）

- 共用消息外壳从原ETF组件提取，实际复用同一份拖拽、边界限制、收起手柄和Home复位逻辑；行情、财务、候选任务独立保存状态，正文不常驻进度卡片。财务范围确认展示指数名称及日期，结果15家公司分页，显示研究日期、8项盈利条件、金额和来源日期，尚未处理与资料待补分开。
- 独立规格及质量审查均完成，发现的旧latest失败覆盖新任务、迟到启动关闭新弹窗、共享候选run跨池显示、来源异常丢失已修复并复审通过。新增5项边界回归先红5失败，修复后关联17项通过；来源诊断仅公开受控code/message，429停止标记保留，不公开本地路径。
- 定向模块78项通过；Changed门禁曾取得430通过、1跳过、4失败（3项映射断言及1项旧CSS版本断言）。修正后受影响16项通过；按钮组调整后管理/切池13项通过。Ruff通过，定向Pyright通过；最后暂存门禁结果以本次提交记录为准。未执行全量回归。
- 标准CLI `D:/code/stock_robot/.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765` 真实启动。正式页面鼠标实测展开窗口左上由(816,666)移动到(516,452)，收起后手柄也可拖动，Home复位正常。真实财务结果、未通过公司的8项条件、来源链接/披露/抓取时间、第二页及范围确认已查看。1280与390宽查看各功能页截图，均无文档水平溢出，标题桌面x234（侧栏后24）、窄屏x12；手机财务结果窗口宽390、左右0/390。截图保留tmp，不提交。
- 真实会话发送“你好”得到缺少llm.api_key的可见响应，未调用外部模型，不宣称有效模型生成。此次未修改会话/SSE逻辑。
- 浏览器使用隔离工作树数据：冻结的2026-10-16范围及2026-10-18结构化产物，经逐份SHA/长度校验在真实runner离线重放红利池578家，盈利数值通过463、不满足115、待补0，财务外部请求0。复核日期仍为10月18日，不冒充10月23日新采集。记录在tmp/financial-web-acceptance-20261022.json。没有审核、发布或淘汰正式成员；全626范围此前492/134与此红利子范围统计不可混用。
- 同主题文档已收敛为现有一份设计和一份实施，移除未提交的阶段文档。后续完整资格、分红价值/成长趋势条件及正式成员验收仍属于后续工作，不能将本次手动财务任务交付表述为全部选股功能完成。

最终暂存门禁：`./scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`通过，Ruff通过、Pyright零错误零警告、435 passed、1 skipped（110.97秒），4条既有依赖弃用警告。门禁日志保留`tmp/pytest/financial-final-*/verify.log`；运行临时目录由脚本成功清理。提交前仅追加此验证记录，源码/测试内容与门禁一致，补做暂存diff检查，不重复业务门禁。


### 完整资格接入前的数据缺口核对

在既定指数并集内离线核对626家公司，复用`screen_saved_batch`验证现有产物并复算，研究截点保持2026-10-18。范围SHA256仍为83e3ba27530991da93ab20c5eb0f6251bc38ab83fae8ea46800036faaef2e93b。脚本`tmp/audit-stock-common-gaps.py`和结果`tmp/stock-common-gap-audit.json`保留，不联网、不写业务库；未运行新正式筛选。

红利池来源为中证A500/中证红利/红利低波，578家中盈利数值通过463、不满足115；成长池来源为中证A500/创业板指/科创50，548家中通过421、不满足127。两池重叠，不相加；全并集通过492、不满足134、资料待补0。

当前工作树研究库只含1252条research_hint，未含已确认规范事实。旧共同资格引擎所有条件均待核验（626家），其中8项盈利条件也未消费结构化初筛结果。结论是现有结构化结果尚未接入旧资格入口，不能据此认定626家公司不合格，也不能把未被旧引擎使用等同于底层数据全部缺失。

建议下一步以独立、可追溯的程序化输入接入已校验的结构化盈利结果，复用现有规则；不伪造reviewer或confirmed记录、不将数值通过写成financial_verified。证券身份、上市年限、市值与流动性优先核对已有适配器，补采范围、具体端点及必要预算核实后再执行；龙头、治理审计及金融专项无可靠结构化依据时继续待核验，不默认放行。此计算入口与旧逐条人工确认门禁的衔接属于需明确确认的行为变化，尚未改正式业务代码、启用正式筛选或发布成员。


#### 已确认的结构化盈利接入方案

用户已确认固定原指数范围，从校验过的产物重算8项盈利条件并接入资格报告，其余缺依据继续待核验；不新增采集、不伪造人工确认、不改成员或界面。

实施顺序：1）新增独立离线资格报告入口，显式输入指数范围、财务采集摘要、只读研究库和目录外输出；校验范围文件SHA与财务manifest输入绑定、公司集合/名称、日期、池归属，不使用全研究库替代范围。2）复用screen_saved_batch校验并重算，复用evaluate_common_admission计算其他共同条件，8项盈利通过显式来源渠道合并；保留原人工计算报告及出处，不生成confirmed或reviewer。既有确定失败不能被结构化通过抹去；缺失/损坏产物为待核验。3）报告按共同资格失败优先归并，不自动放行，financial_verified/formal_screening_enabled保持false，成员发布0；按池另计数。4）测试先行覆盖身份/日期/范围/损坏、8项映射、确定失败、待核验和数据库无副作用；离线运行固定626样本，定向检查与独立合并审查，通过后完整事项本地提交。沿用现有隔离工作树，不修改UI或风格规则。


#### 结构化资格报告接入验证

新增独立CLI `radar stock-structured-admission --scope <原范围JSON> --financial-summary <采集summary.json> --db <只读研究库> --output <独立JSON>`，仅生成共同资格研究报告，不写旧评估存储。通过原范围SHA/财务manifest绑定与公司名称/集合、两池指数组合及归属、日期校验后复用原始财务产物重算；逐公司保留manual_common、structured_financial及合并checks，版本structured-common-research-v1，非盈利条件保持原样。跨渠道正负冲突项待核验，人工原有确定失败仍保留总失败；结构化缺失不回退旧人工通过。

先红缺模块后新增18项测试通过，旧财务screen CLI6项及映射88项通过；随后Changed门禁Ruff通过、Pyright零错误零警告、253项通过（65.80秒），未全量。日志`tmp/pytest/structured-admission-gate-*/verify.log`。范围、日期、代码/名称、损坏、未尝试、双向冲突、其他条件不变、输出保护及库只读有定向覆盖。

真实命令以`tmp/structured-scope-626-input.json`、`tmp/structured-scope-626-20261018/summary.json`及当前工作树`.stock_robot/radar.db`生成`tmp/structured-common-admission.json`，626家完整资格研究结果passed=0、failed=134、pending=492；红利578家failed=115/pending=463，成长548家failed=127/pending=421。研究截点2026-10-18、范围日期2026-10-16，数据库主文件SHA256运行前后相同；本次无网络请求及业务写入、无成员发布，报告formal_screening_enabled=false。这只完成盈利结果接入，其他资格及两池风格数据仍待后续补齐。


A类独立合并规格/质量审查发现2项P2：研究库默认按运行日判过期造成历史截点漂移；摘要二次读取SHA可能与计算快照不一致。已按审查小修：store clock绑定cutoff北京时间当日末，summary/manifest在读取前、重算后及报告构建结束验证原始字节一致，SHA仅取已校验快照。新增历史有效人工失败、summary替换、manifest替换回归先红后绿，最终新模块21项通过，Ruff/Pyright通过。源修改影响有限，由主会话核对与复用先前253项门禁及修后相关检查，不重复全量。修后真实626离线命令再次复核，仍为0通过/134失败/492待核验。

#### 证券名录与结构化市值缺口调查（2026-10-23）

本次为只读调查和隔离探针，未修改业务代码、资格报告或成员。沿用冻结626家公司、2026-10-16范围及2026-10-18研究截点，不将10月23日获取的历史行情称为当时已可得的历史快照。

- 对旧工作树3份沪深官方名录缓存重新校验SHA256并使用现有解析器读取；深交所2906行、沪主板1702行、科创板617行，缓存抓取于2026-10-15，解析均完整。在626家公司中全部有唯一代码匹配和有效A股上市日期。原始名称613家一致、8家仅空格/全角字符差异、5家带XD前缀且部分截短；仅记录差异，不通过删除前缀推断公司身份，也不确认发行人类型。
- 按既有完整三周年规则离线核算：并集618家满足、8家未满三年；红利578家均满足，成长540家满足/8家未满。8家中3家已有盈利失败、5家原共同资格待核验。该结果仅为字段与规则实验，尚未合入正式计算通道。审计明细`tmp/stock-market-input-gap-audit.json`，本地脚本`tmp/audit-stock-market-inputs.py`；名称分类及三周年补充由本次PowerShell内嵌Python计算并保存结果。
- 现有Sina未复权日线采集器保留原始成交额，但当前已查缓存仅覆盖600900；现有巨潮股本缓存已查覆盖600900/601318，解析器明确标记单位及全股类覆盖未核实，不能直接计算公司总市值。尚未验证其他临时探针目录的完整覆盖。
- 检查本地已安装AkShare的stock_value_em源码，复用其东方财富RPT_VALUEANALYSIS_DET结构化端点，临时收窄日期为2026-07-01至2026-10-16、单页80行。样本600900/300750/600036/688981，每家一次HTTP请求，超时15秒、不重试、不跟重定向、429停止来源。实际4次均HTTP200，各72行/72唯一日期、1页，响应合计167585字节；无业务写入。原响应、SHA256及请求参数保留`tmp/market-value-source-probe/`，脚本`tmp/probe-market-value.py`。
- 对288行以Decimal核算，TOTAL_MARKET_CAP均精确等于CLOSE_PRICE乘TOTAL_SHARES；接口无成交额字段。此为样本数值关系，不能证明金额单位、全市场历史适用性或多股类逐类估价口径；尤其不能直接声称符合原company_all_shares/share_classes_complete规则。

待用户决定的具体取舍：建议以供应商明确标识的“总市值”作为规模指标，保留200亿元及60日均值门槛，并在报告区分供应商口径与各股类逐类估价合计；这改变原严格市值定义，必须先确认。若保留原定义，口径未证实的公司仍待核验。未获确认前不改业务规则，不扩大到626家公司市值或日线采集，也不自动建立更重的跨市场估价路线。后续完整接入方案须同时明确单位核验、独立交易日窗口、缺失/冲突处理及采集预算。

2026-10-27用户确认采用供应商总市值作为规模指标，保留当前值和近60个交易日均值均不低于200亿元的门槛，报告明确标注供应商口径，不将其表述为各股类逐类估值合计。此选择不证明字段单位或历史覆盖，也不授权新增626家批量采集；正式接入前仍需提出单位核验、交易日窗口、缺失及冲突处理、来源和采集预算的完整方案。

2026-10-27只读缓存审计：主仓XSHG日历缓存来源`akshare:sina:XSHG`，2026-10-04抓取、覆盖至2026-12-31；截至2026-10-18的60个已收盘交易日为2026-07-17至2026-10-16。旧估值原始分页与冻结范围交集188家公司，长度/SHA及单页数量、公司代码核对通过，均覆盖窗口前56日、缺最后4日；另438家没有该旧分页。四公司隔离探针均在这188家中，响应长度/SHA通过且各有60个窗口日期。故在仅考虑已审计缓存时，184家缺4日、438家缺完整窗口，4家日期覆盖完整；此为字段和日期覆盖，不等于单位、冲突或最终筛选结论通过。明细`tmp/stock-market-cache-audit-20261027.json`，脚本`tmp/audit-stock-market-cache-20261027.py`；零业务接口请求、零业务库写入。东方财富个股估值页面将总市值标为元（https://data.eastmoney.com/gzfx/hy/016057.html），但尚未找到`RPT_VALUEANALYSIS_DET.TOTAL_MARKET_CAP`正式字段契约，页面标签及样本数值关系只能作为支持证据，不宣称接口单位已获直接证明。旧响应与探针均为事后抓取，不能冒充研究截点当时可得快照。

用户随后授权上述622家缺口补采，预算最多1866次请求；串行、15秒超时、最多两次重试及3/10秒退避、429停止。临时脚本`tmp/collect-stock-market-gap-20261027.py`先用离线替身验证429停止和重启保护，随后实际622次请求全部完成、无重试或429；原始响应与原子清单存于`tmp/market-value-gap-20261027/`，未写业务库。独立离线脚本`tmp/audit-stock-market-gap-20261027.py`复读原始响应并检查长度/SHA、公司代码、重复日期及非负有效市值；旧响应、四家探针与补采拼接后626家均有完整60日，0家缺日或无效。完整性不等于通过门槛或正式资格。东方财富长江电力逐日估值明细（https://data.eastmoney.com/gzfx/detail/600900.html）直接标注总市值单位为元，支持单位判断，但仍未取得接口字段的直接正式契约。抓取发生在研究截点之后，保留实际抓取时间，不能称为历史时点实时快照。正式计算、归档及成员发布均未执行。

#### 供应商市值接入实施步骤

依据用户确认的供应商口径、窗口与补采范围：1）建立只读、可追溯的供应商市值产物适配与计算，绑定冻结范围、研究截点、独立交易日日历和原始响应长度/SHA；逐公司核验身份、日期、重复、金额及60日完整性，原始和抓取时间可追溯，先用真实样本和失败边界测试。2）保守合并共同资格的两项市值：确定的旧人工失败不能被抹去，正负冲突显式待核验，缺失不能回退有利结论；不伪造人工确认，现有CLI默认行为兼容。3）接现有详情、风格共同结论及评估归档时绑定来源身份并复核新鲜度；历史截点的结果只作历史参考，不能冒充当前评估。4）离线复核固定626家，报告分别统计每项通过、不满足和待核验，正式筛选及发布保持关闭。测试覆盖范围/身份/日期/单位/摘要、缺失冲突、门槛、历史和来源变化；相关范围检查并按S级独立规格和质量审查。真实页面有改动时按项目要求验收。完成后同一事项收敛提交，不追加采集。

#### 市值来源接入及复核（2026-10-28）

#### 上市年限与行业缓存接入实施（2026-10-29）

用户已确认固定626家范围，复用已保存官方上市名录与申万行业资料，不新增采集。实施顺序：先为只读登记源建立测试，校验范围、文件长度/SHA、来源日期、公司代码与名称差异；复用官方解析器计算完整三周年，仅替换上市年限条件，身份/发行人类型及龙头仍保持原有核验要求。行业仅作带日期来源的分类参考，不提升龙头资格。其次接入共同资格、详情、草案和归档来源复读，历史只作参考，保留人工失败和冲突；最后离线核对626家、定向检查与独立规格/质量审查、真实服务验收，完整事项本地提交。原始响应、缓存、截图与审计明细只留本地，不提交。

完成：旧乐咕乐股成分采集曾遇429，仅存7个行业页；完整分类来自已保存的申万官方历史XLS和2021对照XLSX，均按旧manifest校验长度/SHA并重新解析，原始抓取时间未知如实标注。官方名录及行业登记复制到本地状态目录，不提交原始文件。2026-10-18截点离线626家：上市满三年618、不满8；行业来源626可读取，网络请求0，非正式入池。输出`tmp/company-profile-replay-20261029.json`。

独立规格审查发现名称差异页面遗漏，已补原名录名称和待核验提示；质量审查通过。暂存Changed门禁Ruff通过、Pyright 0错误0警告、615项通过，4条既有依赖弃用提示，日志`tmp/pytest/profile-staged-47060-e0568c7a28564d338a2ca4380c861d3c/verify.log`。真实标准CLI隔离服务中，浏览器查看平安银行历史上市日、三周年及行业参考，本次资格仍待核验，浏览器error日志为空；会话发送有可见思考/计划状态，未证明有效LLM生成。未发布成员，未新增网络采集。

新增只读登记、范围/日历/响应复核与市值计算；详情、共同条件、风格及草案归档接同一来源，归档保存与复读校验来源和市场窗口，旧规则版本档案失效。未记录的旧响应抓取时间明确保留未知，禁止伪造；缺失或损坏不放行。双方冲突的单项待核验，既有人工确定失败仍保留；历史结果仅参考。均值展示保留两位小数并标约数，门槛仍以未舍入总额判断。正式筛选、发布和自动任务保持关闭。

隔离离线复读626家公司：当前市值522通过/104不满足/0待核验；60日均值536通过/90不满足/0待核验。两项计数分开，不可相加，也不代表正式入池。完整输出`tmp/stock-market-value-replay-20261027.json`，研究截点2026-10-18，原始数据抓取在截点后。目录和日历经摘要校验后登记到本工作树`.stock_robot/stock-market-value/`，日历表原无XSHG记录，插入既有缓存、未覆盖；10月28日核对000001、600900、688981均显示历史参考。原始响应和登记数据不提交。

独立规格、质量审查及修后复审：日历变化、旧档案规则版本、重复JSON、深嵌套、未来时间和页面历史明细已修；另修归档直接调用时日历窗口不一致，以及均值循环小数过长。真实标准CLI在隔离状态目录运行，浏览器显示000001的历史市值和均值约数、未舍入判断说明，本次条件仍待核验；真实会话发送得到可见0/1计划摘要，日志显示LangChain模型未注入、降级响应，不称有效模型生成。隔离库复制范围产物并重绑本地路径以避免覆盖原库；未发布成员。窗口符号链接测试因本机权限不足跳过，其他结果以提交门禁为准。

暂存Changed门禁Ruff通过、Pyright 0错误0警告；pytest 627通过、1跳过、1失败，失败是`test_stock_admission.py`仍断言旧规则版本v1。将该断言更新为v2后，受影响文件80项通过，Ruff及使用门禁配置的Pyright均通过；其他源码与门禁运行时一致，不重复全量。失败及完整输出保留`tmp/pytest/market-value-staged-11360-701bc55df94e4bfd904a5874e58c963d/verify.log`。最终代码再次离线复算626家，统计与上文一致，网络请求0；浏览器控制台error为空。

#### 现有成果与真实缺口复核（2026-10-24）

用户指出此前进度说明遗漏已完成筛选。本次仅核对提交、代码、历史研究资产和定向测试，不重新采集、不改变规则或成员。撤回未经此核对得出的“还需三阶段、4—9个工作日”估算；它把已完成的生命周期及管理页重复算作新开发，不能作为交付承诺。

| 能力 | 已核实成果 | 真实边界/缺口 |
| --- | --- | --- |
| 早期规模与盈利初筛 | 2026-10-13全市场研究得到685家数值候选；原脚本、响应、清单均保留 | 后续范围已改为固定指数组合，不能继续使用685家替代当前626家 |
| 行业、现金流、成长及分红研究 | 685家研究表仍在；含行业、年度现金流、季度增长、分红事件和审计摘要，非仅盈利 | 与当前范围交集417家；历史建议口径、日期和规范事实审核要求不同，不能直接授予本轮通过。官方行业缓存按代码覆盖当前626家，但不等于行业/龙头已确认 |
| 历史行情、估值与趋势研究 | 238家完成行情/估值研究，价格和估值原响应仍在；曾计算固定60/80日窗口、均线和估值覆盖 | 与当前范围交集188家，截点2026-10-12；相对收益、估值适用性等未完成，不能当2026-10-18行情。此前把供应商市值接口视为新发现不完整，旧脚本已使用相同RPT_VALUEANALYSIS_DET端点 |
| 固定指数范围及新财务筛选 | f265c85固定两池指数组合；e02dbcc/a608a55完成结构化采集与8项筛选，626家492通过/134不满足 | 626只是指数范围；492是盈利条件通过，不是完整入池人数；无需重做该采集 |
| 消息组件及手动财务网页 | 01ca524完成独立任务、范围冻结、恢复、15项分页及原拖拽组件复用，已有真实浏览器验收 | 财务弹窗展示已接通；公司详情与草案计算仍调用旧evaluate_store，未消费新结构化财务通道 |
| 独立共同资格报告 | 1762611已将结构化盈利接入只读报告，其他条件保留原结论 | 新入口为离线common报告，无styles/评估归档/网页接线；不得称为最终完整资格已接通 |
| 两池风格引擎 | d37eb24已有现金流区分、三年实施分红、流动性参考及金融分支，非尚未开始 | style.py仍列红利支付率/股息率/价值估值、成长增速/估值/趋势待配置；历史实验脚本已有部分数值算法，应先审查复用。代码未配置不等于用户未讨论或必须重问已确认参数 |
| 成员生命周期、管理与后台 | c0640bd已实现新增/暂停/退出/恢复、事件、CAS/幂等和双池独立；d3b8d65已有管理/审核/草案/API；1b9194a已有手动队列 | 正式发布门槛有意关闭，成员存储不是自动风险识别或自动轮换；行情任务只更新资料，不能称自动淘汰已完成，也无需重写成员存储 |
| 主分支集成 | 当前工作树1762611包含上述生命周期实现；git merge-base确认c0640bd是其祖先 | 1762611尚未合入dev；此为尚未集成，不是实现丢失。未获本次合并授权，不操作主工作区已有其他主题改动 |

历史资产核对：`D:/code/stock_robot/tmp/stock-radar-market-20261013/manifest.json`含1435条记录。本次选取研究表/摘要/行业表/相关脚本及prices-20261012、valuations-20261012目录原响应，共966份，逐文件长度及SHA256与原清单全部一致；并非对1435项全部声明通过。脚本`tmp/audit-existing-stock-work.py`及结果`tmp/stock-existing-work-audit.json`保留。执行中首次查看manifest误按字典访问触发AttributeError，修正为实际list结构后完成核对；无业务库写入及网络请求。

定向复核命令：共享venv Python以当前工作树PYTHONPATH执行`-m pytest tests/radar/test_stock_membership_store.py tests/radar/test_stock_style.py tests/radar/test_stock_structured_admission.py tests/api/test_stock_radar_management.py -q`，使用独立`tmp/pytest/progress-audit-7a26aadb636a4aff91518ffa7c116cd2/base`与同次cache。105 passed（7.18秒），1条既有Starlette/httpx弃用警告。未修改业务代码，未运行全量或重复真实浏览器验收；本次测试不代替全流程真实发布验收。

后续工作应聚焦已有资产的接线和缺口补齐：先复用历史结构化研究与已实现模块，明确截点/当前范围/口径兼容性，再补数据；不因旧评估入口pending就声称底层全部没有。新财务结果与公司详情/评估/草案接通属于后续具体方案，不能在本次只读核对中擅自修改。正式规则关闭、龙头/治理等未核验、自动轮换未执行及未合入dev均仍为真实边界。

### 财务产物接通公司详情与关注草案

#### 成长路径研究参考线（2026-10-29）

用户确认旧研究的两条建议参数作为研究参考线，不自动淘汰或发布：持续成长为TTM收入同比≥10%、扣非归母同比≥15%，及最近两独立季度收入和扣非同比均>0；经营改善为两季度上述同比均>0且扣非利润率同比均改善。先复用已保存626家结构化main原响应与冻结范围，校验摘要、身份、公告/更新时间、币种、报告期及修订冲突；以累计差分和年度加减计算，基数非正、缺失或不可比保持待核验。输出逐项数字及来源/截点、两路径参考状态，不作为资格或正式成员结论。随后接已有风格研究与详情，历史数据仅参考，绑定归档来源；测试先行、独立审查、真实服务验证及范围门禁，不另行采集。

依据：用户在现有成果复核后确认继续推进“接通结果”，复用既有财务任务、公司详情、评估、审核草案，不重采或更改入池规则。此事项仅补现有流程的数据接线，不恢复PDF路线、不开正式发布、不配置待定风格/市值阈值。

#### 具体边界与实施顺序

1. 新增共用只读财务来源适配器：按公司读取数据库已保存财务任务，目标截点不晚于本次评估；按研究日期及同日创建顺序选择最新包含该公司的任务。新任务尚未处理、失败或产物缺失不回退到旧的有利结果；未来截点不得参与。校验冻结范围、任务规则版本、产物路径/长度/SHA及公司/日期，复用既有原始collection重算，不信任已保存passed标志。无任务表的GET不创建数据库或目录。对外不泄露原始缓存路径。
2. 将原离线入口的8项合并逻辑提为共用计算。evaluate_store可显式接受服务器读取的财务来源，原CLI无此输入保持行为不变。只有同截点且有效的结构化结果参与共同资格，原人工确定失败不能被抹去；冲突保留双方。历史/版本不符/无产物仅保留参考与受控诊断，相关盈利不得借结构化旧通过被提升。风格计算消费合并后的共同结论，不改变原现金流专项或其他风格条件，不因盈利通过自动提升风格资格。
3. 公司详情和关注草案使用同一适配器与计算路径。归档输入保存来源任务、范围与产物身份；存档和assert_current检查对应来源未更换或损坏，防止财务产物更新后仅公司revision不变而误认旧档案当前。请求重放/公司revision/CAS与正式发布门槛不变。未知或历史参考能保留研究草案，但不伪装完成当前盈利复核。
4. 复用详情现有共同条件折叠区及样式，只显示必要的财务截点、历史参考/待补原因与来源渠道，不新增窗口、按钮或改变页面结构。既有财务结果弹窗不替代；静态资源变更同步缓存版本。
5. 测试先行覆盖：只读无表/无任务；双池同公司与未来任务；新任务未处理/损坏不回退；身份/截点/版本/哈希；只替换8项；双向冲突及共同失败向风格传播；历史不冒充当前；详情和草案一致；财务来源变化导致档案过期；重试幂等/成员不变。定向Ruff/Pyright/pytest及必要Changed门禁；S级独立规格与质量审查，修复后复核。真实CLI服务和浏览器查看旧快照日期、条件展开、原财务弹窗、窄屏并完成一次真实发送响应，不新增外部采集。提交完整事项，未验证数据仍待核验，不将此项表述为全部股票池完成。

#### 已存成长字段覆盖核对（接线期间，只读）

避免后续重复采集，对626份既有财务产物及其main原始缓存逐份核对长度与SHA256，读取原始接口已缓存的历史响应（collection本身只保留5个目标期）。全部626份为完整单页；按各公司latest_available_period及公告日不晚于2026-10-18选择目标期，其中618家最新半年报、8家三季报。最近两独立季度及同比所需累计期间的收入TOTALOPERATEREVE与扣非KCFJCXSYJLR非空覆盖626家；收入TTM同比所需期间非空覆盖624家，缺失为688795/688820。

这是字段与期间覆盖实验，尚未核验计算算法、不同报表金额口径/冲突或成长门槛，不能记为626家成长通过，也不自动替代原利润表主源。最近两独立季度覆盖检查包含最新季度及前一季度各自必要的累计减数与上年同期，不只检查半年/三季累计。脚本`tmp/audit-growth-cache-coverage.py`及结果`tmp/stock-growth-cache-coverage.json`保留，网络请求0、业务写入0。后续成长数值工作优先复用这批已缓存原始响应，先审查旧analyze_styles.py算法，无须默认重采。

#### 财务详情/草案接线交付与验证

新增`financial_source`只读来源适配器及`financial_admission`共用保守合并；原CLI继续兼容merge_profitability导入，默认旧评估命令行为不变。API详情与关注草案接入同一来源；评估输入绑定财务截点/任务/范围/产物身份，保存及freshness均复读校验。style消费合并后的共同条件，其他风格条件不改变。页面仅在原共同条件区补来源、三类日期及逐项历史参考；JS资源链同步至20261024-financial-source-2。

测试先红缺模块后完成加载器/合并/API/档案/UI覆盖。初版关联组228通过、3条验证映射精确断言失败，修正映射后复核通过；最后独立审查发现P2：在完整冻结范围验证前按公司归属跳过，BROKEN字符串或空列表可能回退旧通过。将完整_financial_snapshot验证前移，加入空范围、非法代码、缺名与合法无关范围正例。真实浏览器发现历史行泛称资料待补，改为逐项保留reference_as_of/reference_check，显示历史日期及当时结论仅供参考；本次outcome仍pending，历史failed亦不冒充本次失败。两类补强先红7项，修后相关152项通过，追加API精确断言1项通过。规格与质量双审及修后复审均PASS，质量复审9个定向探针通过。

最终暂存门禁：`./scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`，Ruff通过、Pyright零错误零警告，702 passed（102.45秒）、4条既有依赖弃用警告；不是全仓回归。日志`tmp/pytest/bridge-gate-0fc67921fe314c9e97d5e8630d83958c/verify.log`，该门禁本次运行目录自动清理成功。源码及测试与门禁一致，随后仅追加本验证说明，补暂存diff检查。

真实标准CLI服务在隔离工作树启动，当前监听8765。旧离线验收财务任务使用browser-acceptance-structured-v1专用版本标签，保留不改；通过临时脚本`tmp/replay-financial-bridge-live.py`在同一隔离验收库用当前正常规则版本、原冻结2026-10-16范围与已校验的2026-10-18原始产物离线重放两池，不新增行情/财务HTTP请求。红利578家463通过/115不满足，成长548家421通过/127不满足，共1126池内条目（公司重叠），并非1126家去重公司。网络请求0；重放前后stocks_reviews、stocks_memberships、stocks_evaluations均0。记录`tmp/financial-bridge-live-replay-20261024.json`；未修改审核或正式成员，不将其当10月24日新采集。

真实API与浏览器验证600900/300750的财务来源status=historical、实际财务日期10月18日；详情评估日10月24日、公司资料日10月23日、冻结范围日10月16日分别保留。长江电力8项显示10月18日当时通过，仅供参考，本次资格仍待核验。原消息窗财务结果仍显示578处理完成、463/115/0及分页。1280宽标题x234、页面scrollWidth1280；390宽标题x12、scrollWidth390，日期说明宽321.33px、左右26.67/348，无水平溢出，查看实际截图并恢复正常视口。截图`D:/code/stock_robot/tmp/stock-financial-bridge-wide.png`及`stock-financial-bridge-mobile.png`保留，不提交；前端error日志为空。

真实会话发送“你好，请只回复‘已收到’”得到可见计划摘要“完成0/1步骤”；服务日志说明LangChain模型未注入、降级计划，历史消息记无候选工具。此项只证明真实发送及可见降级响应，不宣称有效模型生成；本事项未修改会话/SSE实现。保留原会话未发送草稿，另开正式页面标签验收。

风险及范围：同截点财务只解决8项计算接线；较早快照仅参考，不能据此宣称当前完整资格通过。龙头、治理、市场/估值及未配置风格仍需后续补齐，正式发布和自动轮换未启用，未合并dev。未重复原全功能页布局或图表验收（此次未改布局/CSS/图表）。子代理尝试清理成功测试base/cache的命令被自动审批以blocked by policy拒绝，未重试，相关临时产物保留；不影响已通过的门禁或源码交付。

#### 成长路径研究接线交付与验证（2026-11-01）

实现确定性季度差分、TTM及两条已确认参考线，新增离线批量复用和只读成长来源登记加载器；校验范围/原响应长度与SHA256、证券身份、币种、公告及更新时间、完整分页和冲突修订。详情、风格研究和评估归档共用来源，归档复读且绑定嵌套展示，规则版本升级使旧档案失效。页面沿用原折叠区及视觉体系，明确历史参考；不改变资格、机会、正式规则或成员。静态缓存链更新为20261031-growth-research-1。

626家公司实际登记加载器离线重放全部可读：持续成长118达到参考线、398未达到、110待核验；经营改善122达到、405未达到、99待核验，两组不可相加。研究截点2026-10-18，今天仅作历史参考。复用既有原响应，新增网络采集0。结果保留tmp/growth-registry-replay-20261031.json；校验后的登记复制到本工作树忽略目录.stock_robot/stock-growth，原始数据不提交。

规格及质量独立审查发现并修复：嵌套成长展示与归档来源绑定不足、非法原始行触发AttributeError、证券后缀身份与批量抓取元数据校验缺口。新增季度跨年、非法行/身份、嵌套篡改及来源变化回归。暂存Changed门禁Ruff通过、Pyright零错误零警告，pytest604通过/1失败；唯一失败为既有pool_null异常输入，新增校验误对空池调用get。补类型判断后，evaluation_store及三个成长测试文件39项通过（3.61秒），相关Ruff通过；最终evaluation_store使用同次门禁配置执行venv Python -m pyright，零错误零警告。复用未变代码604项通过结果，未重复全仓门禁。首个完整异常与输出保留tmp/pytest/growth-gate-31804-4f733e877dd74c4b916a404360619fec/verify.log。

真实venv stock-robot run路径在隔离验收库启动。8765属于其他任务服务、8766亦被占用，为避免干扰使用18765，未宣称完成8765端口验收。浏览器进入成长趋势池/候选管理/平安银行，展开成长研究：显示历史截点10月18日、TTM收入-4.515358%/扣非归母-1.278254%，2026年一二季度收入/扣非同比及利润率变化；共同及成长条件仍待核验、正式成员为空。检查实际渲染截图、折叠交互，浏览器error日志为空。真实发送“你好，成长研究验收”得到可见目标及完成0/1步骤摘要，模型未注入的降级响应，不宣称有效模型生成。此次未改布局、CSS、图表或会话实现，未重复全功能页布局验收。

剩余边界：参考线仅供研究；非正基数及缺失/冲突数据保留待核验。龙头、治理、估值和价格趋势等尚未完成，未发布成员、未启用自动轮换、未合并dev。

#### 同步 dev 公共基础（2026-11-01）

用户授权同步本地dev，以merge保留双方历史。同步前本分支6f10f39、dev a55a4c0，双方独有37/122提交。合入统一采集Phase 0–3、分析模式及版本0.3.2等dev既有成果，不在此次顺带迁移股票池专用采集。冲突共6文件：历史实施记录采用dev整理版并补历史适用范围与后续记录链接；CSS保留双方独立作用域；HTML缓存链统一20261101-dev-sync-1；CLI合并双方导入与命令；静态页及验证映射测试保留双方覆盖。自动合并的API、任务映射及入口差异由主会话复核，未派发子代理。

合并前显式运行 .\scripts\verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv：全量Ruff通过、Pyright零错误零警告、数据取数入口检查通过；pytest4901通过、7跳过、2失败（389.73秒）。完整输出tmp/dev-sync-20261101/full-check.log和同次唯一pytest目录保留。两个失败为dev既有test_hk_live_regression参数用例与新分析模式不一致：仍默认要求评分，且预期旧信号文案。本次仅在测试显式开启analysis.extra_enabled并更新为当前“模型信号：暂未生成”，保留原始事实、评分、结构化signal为空及归档断言；不改变生产默认行为。第一次定向复核确认评分通过后暴露旧文案，最终tests/test_hk_live_regression.py、tests/core/test_analysis_mode.py、tests/report/test_hk_live_report.py合计28通过（4.05秒）；改动测试Ruff及使用同次门禁配置的Pyright通过。未变代码复用全量结果，不宣称最后整套重新全绿。

真实venv stock-robot run --host 127.0.0.1 --port 8765在隔离验收目录启动。浏览器验证四池选择器、成长候选548项、平安银行详情和成长研究折叠：研究截点10月18日及历史参考说明保留，成员仍为空、正式发布关闭；实际渲染截图已检查，前端error为空。真实发送“你好，分支同步验收，请回复已收到”，得到可见目标与完成0/1步骤降级摘要，未注入模型，不称有效模型生成；服务日志确认新CSS、app入口及成长模块加载。未新增布局或业务行为，未重做全功能页布局/图表验收。

同步只把公共基础带入本分支；股票池专用采集向统一结果契约的适配仍属后续工作，不能将入口静态检查通过表述为所有requests调用均已统一。未推送、未合并回dev、未启用调度或发布成员。

### 已确认：股票池统一结果与财务—成长闭环（2026-11-01）

用户确认设计文末方案后实施，沿用当前独立工作树；不新建计划。任务顺序与验证：

1. 先添加共享结果边界和财务任务成长选择回归，验证全失败/部分/缓存原时间、合法main部分可算、最新任务阻断旧登记。用原入口及实际共享ValueResult做离线探针，先取得失败。
2. 四个现有采集器增加result入口，返回ValueResult[dict]及共享DataCollectionRecord；原dict接口保持。来源组件逐项记录原时间/cache状态，批次混合来源不伪造统一抓取日期。StockUpdateRunner默认消费新入口，保留旧注入兼容，记录随不可变产物归档；不增加重试。
3. 抽取财务任务选择和已保存产物读取；盈利与成长各自校验业务必需字段，成长复读完整main原文。load_growth_source优先任务，只有无适用任务才读取旧登记；失败/损坏新任务阻断回退。API与归档复用同一入口，必要时提升风格/归档版本使旧绑定失效。既有已完成任务无需重采可立即重算。
4. 更新verify映射和相关回归；主会话验证稳定后按S级安排规格/质量独立审查，修复小问题由主会话复核，避免重复代理调查。
5. 626份现有原文离线重放、请求计数0并比较统计；真实2公司财务冒烟最多10类逻辑请求/30次含重试尝试，使用隔离目录。标准8765真实服务完成任务到详情以及发送响应验收；未改变布局，不重做全部功能页布局。
6. 范围Ruff、Pyright、pytest与暂存差异检查后按完整事项本地提交。无推送、合并回dev、调度或发布授权；全仓检查复用上次同步记录，除非改动证明需要扩大。

#### 统一结果与财务—成长闭环交付

四条专用采集新增collect_*_result，复用ValueResult/DataCollectionRecord/make_record；旧dict入口、来源、串行和既有请求预算保留。组件记录原时间及live/cache，混合批次不伪造统一抓取时间；明确全失败、校验失败、正常空和部分成功的差别。runner默认使用新边界并在不可变产物/任务结果保存记录，旧测试桩仍支持dict但不能只写complete冒充有效数据。财务缓存命中保留原抓取时间，元数据新增cache_status仅用于本次投影。

抽出SavedFinancialTask/load_financial_task供盈利和成长共用选择、范围和产物校验。成长读取该任务完整main原文，不依赖裁切的5期；其他表缺失不阻断可独立计算的成长。已有适用的新任务未处理、损坏、版本异常或main失败时阻断旧登记；无适用任务才读旧登记。归档支持显式数据库名，自定义CLI数据库不再误查radar.db；风格版本升为styles-research-2026-11-01-v3。原折叠区补渠道和受控原因，不增加控件/布局，缓存链更新20261101-stock-task-source-1。未新增数据源、依赖、表、规则或调度。

测试先红：新增结果契约测试因模块缺失失败；实现后45项通过。相关组初次83通过/2失败，失败为旧成员刷新测试桩无数据却称complete，补最小合法数据后范围组247通过/1跳过。Changed检查先发现测试.value缺判空3处，补显式断言后879通过/2跳过。规格、质量各一次独立审查发现3项P2：校验失败后空行误变正常empty、成长加载固定数据库名、页面不显示失败原因/渠道；均修复并新增回归，主会话定向65项通过，无重复审查派发。最终 .\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv：Ruff通过、Pyright零错误零警告、数据入口检查通过，982 passed、2 skipped、1条既有依赖弃用警告（108.06秒）。日志tmp/collection-staged-gate.log；通过目录自动清理。最终业务源码与此门禁一致，随后仅补文档与暂存差异检查；不重复全量。

626家公司原产物经过长度/SHA校验，复制原字节到新隔离任务缓存，仅重绑隔离产物自身路径；两池按冻结范围离线任务回放578+548条，共626家公司，不审核或发布成员。实际task加载器626家均可读，持续成长118达到/398未达到/110待核验，经营改善122达到/405未达到/99待核验，与旧登记重放完全一致。网络请求被阻断且计数0；结果tmp/collection-growth-replay-20261101.json，研究截点仍为2026-10-18。新原文、缓存、脚本与完整输出均在tmp，不提交。

真实采集冒烟选择普通企业600900与银行600036，使用明确标识为isolated-two-company-smoke的2公司样本冻结范围，不称当前完整指数范围。第一次临时脚本遗漏Windows multiprocessing主入口保护，子进程重入并在隔离旧验收库创建样本任务，未取得有效采集；已停止脚本，修复主入口，在新tmp/collection-live-20261101-v2目录从只读备份重新验收，未修改工作树正式业务库。新隔离库剔除首次错误样本任务，只保留本次正常2公司任务。实际10次HTTP、0重试，2家公司财务任务completed；主要指标及其他4类原文均保留。随后以禁止网络回调同截点再读，两家各5组件全为cache、原时间保留，新增请求0。输出tmp/collection-live-smoke-20261101.json与tmp/collection-live-cache-20261101.json。

标准venv stock-robot run --host 127.0.0.1 --port 8765在新隔离目录启动。首次页面因备份库的旧范围产物仍指向原隔离路径而显示可重试503；读取原文并校验SHA/长度后，只复制并重绑新隔离库该范围产物，原库不变。重试恢复，真实浏览器显示财务复核已处理2/2、盈利条件通过2/未通过0/待补0；长江电力详情盈利截点和成长来源均为2026-11-01，成长显示来源“财务复核任务”，TTM收入-0.411914%、扣非归母-3.444341%，最新2026年二/三季度明细可见。新隔离目录无stock-growth目录，证明无需另行登记。正式成员仍为空、发布关闭。已检查实际渲染截图，浏览器前端error为空；发送“你好，财务与成长任务接线验收”得到可见0/1步骤降级摘要，模型未注入，不称有效模型生成。仅文字补充，无布局/图表变化，不重复全页面布局验收。

边界与风险：此次统一结果契约，不把股票专用取数改成报告ResilientCollector，也不引入其TTL/完整快照保护。既有行情和股本强制刷新失败仍可能覆盖其成功缓存索引，原字节仍保留；这一既有缓存行为未在本轮改变，不能称全部公共缓存机制已迁移。市值、上市/行业仍为原只读登记，自动刷新未接；部分成长缺数据仍待核验。未重采626家、未推送、未合并dev、未发布成员或开启定时任务。

### 已确认：基础资料更新任务（2026-11-01）

用户在解释资料任务后确认继续，采用设计文末reference_review方案；产品称基础资料更新。仍使用当前独立工作树和一份计划。执行顺序：

1. 离线探针确认现有名录/行业/市值加载器可接受任务中的不可变登记位置；先写冻结范围、坏新任务阻断旧来源、自定义数据库、缓存保留及恢复测试并取得失败。
2. 新增reference_review冻结快照及来源选择器：两个池去重范围、原范围摘要、来源任务身份；新适用任务优先，任一来源独立成功/失败，不回退更旧有利结果。三类登记加载器复用旧计算器并支持显式db；旧登记仅无适用任务时兼容。
3. 资料采集复用官方名录采集与既有申万/市值解析；请求串行、15秒、最多3次、3/10秒退避，429停止同源；名录4MiB、其他8MiB，完整成功索引与尝试分离。不可变快照按任务/尝试存储，来源完成即保存租约保护的任务进度；恢复不重采成功项。
4. 接StockUpdateRunner、现有CLI/任务API和原详情/草案/归档；无新网页入口、页签或布局，旧队列和原财务按钮不变。规则/归档版本保证旧来源绑定失效。
5. 范围测试、既有626资产离线重放且网络0、至多2公司/7逻辑请求/21次含重试尝试的真实冒烟；真实标准8765详情及发送响应验收。规格与质量各一次独立审查，小修复主会话复核；无重复调查代理。
6. 记录真实失败和范围风险，相关检查及暂存差异通过后整体本地提交；不全量重采，不开启定时或发布，不推送/合并dev。

#### 基础资料更新交付与验证

实现reference_review独立任务、冻结一个/两个池候选、同池/双池去重、按来源保存及继续任务。复用官方3份名录、申万2份原件和既有供应商市值计算；原文、范围、日历窗口、登记和摘要随不可变任务产物保存，不覆盖旧全局登记。完整成功缓存与尝试分离，原抓取日期保留；429停止同来源，其他来源独立处理。名录亦补成功索引保护。新任务优先、坏新任务阻断旧登记；旧目录只在无适用新任务时兼容，支持自定义数据库。详情/草案沿用API及归档加载器，风格规则版本升为styles-research-2026-11-01-v4，缺旧绑定档案失效。行业原段落补受控原因，资源链更新20261101-reference-review-1，无新按钮/布局。

入口：radar stock-reference-update --db <数据库> --pool growth_trend（可重复--pool dividend_value，默认今日，可用--as-of/--force-refresh；继续用--resume-run-id且不更改日期或刷新参数）；入队后radar stock-update-run-one --db <数据库>执行，stock-update-list查看。API为POST /api/v1/radar/stocks/reference-review，pool_ids数组、可选target_date/force_refresh/resume_run_id，沿用原后台线程。任务API返回范围摘要和逐项结果，不返回完整范围、租约令牌或本地路径。没有新增网页触发入口或定时调度。

测试先因reference_task缺失取得红灯，随后冻结/只读选择3项通过；缓存及独立恢复、旧来源兼容等39通过/1跳过，跨层99通过/1跳过。新增入口测试最初2项失败为合成发现任务日期18日与范围16日不一致，修正测试日期后通过；早期相关测试有一次pytest cache目录权限警告，产物保留，不改ACL。新代码Ruff、定向Pyright通过。首次Changed门禁789通过/2跳过；最终暂存Changed门禁863通过/2跳过，Ruff、Pyright零错误零警告及数据入口检查通过（147.51秒），日志tmp/reference-staged-gate.log，成功目录已自动清理。

独立规格/质量各一次审查：修复CLI默认window_loader返回None遮蔽本地日历、行业空/部分覆盖误判complete、50000层损坏JSON穿透详情、单池任务被未选池不完整阻断。加来源唯一覆盖、原始深JSON异常、CLI执行收到日历、所选池标识回归。单池范围用stock-reference-scope-v1及selected_pool_ids，不把未选池partial改成complete；旧完整范围格式保留兼容。离线回放另发现市值复读固定manifest.json而任务文件名不同，已修并新增重命名回归。规格代理窄范围修后复核PASS，未派额外调查代理；修复后相关64通过/1跳过（5.78秒），最新受影响源码/测试Ruff及定向Pyright零错误零警告。复用未变范围863项通过结果，未重复全量；最终仅补文档/暂存差异检查。

626家历史资产在新tmp/reference-offline-20261101隔离库创建真实任务并复制校验过的原字节，源范围、公司名、原SHA/长度/抓取日期保留，网络回调禁止且计数0。最初名录618通过/8不满足、行业626可读断言通过；市值因上述文件名bug未通过。修复后仅复读受影响市值626家，当前522通过/104不满足，60日均值536通过/90不满足，与旧研究一致；不是新取数，不把这些结果当2026-11-01当前资格。摘要tmp/reference-offline-20261101.json，首次完整失败日志tmp/reference-offline.log、修后复核tmp/reference-market-recheck.log保留。

真实冒烟仍仅选600900、600036，使用明确标识isolated-two-company-smoke的2公司样本范围，不宣称其为完整指数范围。新tmp/reference-live-20261101隔离状态目录从既有隔离库只读备份，校验并重绑其既有范围产物，未改正式业务库。初次8次HTTP：3份官方名录成功、申万历史表证书链验证失败3次、2公司市值获取成功但受文件名bug标partial；修复后继续同一任务，跳过已完成名录，只重试2家公司市值，未再次访问已知证书失败的申万源。累计10次HTTP，在21次上限内。最后名录completed、市值2项completed、行业failed，整体partial；日志及来源摘要tmp/reference-live-final-20261101.json。保留证书验证，未换数据源或关闭安全检查，行业失败不回退旧登记。

标准venv stock-robot run --host127.0.0.1 --port8765在新隔离目录启动；真实浏览器原候选管理搜索长江电力，详情显示A股上市日2003-11-18、完整三周年2006-11-18，当前市值727684794873.84元（2026-10-30）、60日均值约699897055621.04元；行业显示基础资料任务待补原因，未被旧登记冒充。已检查实际截图，前端error为空。新隔离库未复制以前财务原文，因此财务/成长按其各自来源规则显示待补，此为隔离验收数据边界，不宣称旧财务已失效；正式库未受影响。真实发送“你好，基础资料更新验收”得到可见目标/0/1步骤降级摘要，模型未注入，不称有效模型生成。无布局/图表改变，不重复全功能页布局验收。

范围与风险：行业获取在本环境受证书链问题阻断，仅离线契约/解析与失败流程通过；任务实现不意味着真实行业刷新成功。未启动626家当前网络采集，未新增源/依赖/数据库迁移，未开启调度或发布正式成员，未推送/合并dev。行情/股本缓存保护已由独立本地修复3c20699完成；本事项按完整任务提交。后续估值、趋势、治理及龙头证据仍不在本轮。

### 已确认：现金流比率离线接线（2026-11-02）

1. 先补结构化年度投影和风格测试：覆盖0.8边界、逐年分母、发行人及口径、人工与结构化逐年冲突、历史和失效来源、金融专项及归档失效。
2. 财务任务重算后公开最近三年安全投影，保留来源及披露日期；风格计算仅在同截点有效任务和已审核普通企业、币种、合并现金流及归母利润口径均成立且逐年金额一致时使用结构化参考。冲突或缺失保留待核验；金融机构继续专项。无财务任务的旧调用维持人工路径，存在失效任务时不得回退人工通过。
3. 调整风格规则版本，复用财务来源归档复读；运行相关测试和静态检查。隔离离线复算626家，记录来源日期、网络及业务写入情况，不发布成员。
4. 审查差异、验证暂存范围并作一笔本地提交；不更新行业、不重采、不启用定时或发布。

#### 现金流比率接线与离线验证

财务投影保留三年现金流及归母利润金额与原来源信息；风格结果仅在已审核普通企业、人民币、合并现金流及归母口径有效，且人工与同截点任务逐年金额一致、主辅源无差异时使用。金额冲突、资料不足、历史或失效任务保留待核验及原因；银行、证券、保险继续类型匹配专项。未有适用财务任务的原人工路径保留。风格版本v5使旧风格归档重新评估；任务来源、日期和摘要继续由现有归档绑定及复读。没有修改页面或行业映射。

先红灯：新增接线测试6失败、1通过，修复后相关三文件109通过；补充非正分母、金融专项、真实任务投影及阈值两池差异。首次集成运行因未预建pytest临时父目录造成22项setup错误、87项通过，使用新目录重跑恢复；一次清理命令被自动执行检查拒绝，未修改ACL，测试目录保留。最终暂存范围门禁 `.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff、Pyright（0错误0警告）、数据入口通过，pytest 280通过；日志`tmp/cash-staged-20261102.log`。主会话核对差异、来源绑定和原详情展示，无独立代理审查。

626家公司使用2026-10-18历史财务任务离线回放；两个池现金比率分别578/548条全部待核验，原因均为缺少有效可追溯审核事实。脚本最初沿用旧缺项说明，核对其结论来自真实风格计算后修正临时脚本说明及来源标记，再完整重跑；结果`tmp/stock-pool-cash-replay-20261102.json`及同名CSV，日志`tmp/stock-pool-cash-replay-verified-20261102.log`。网络0、业务写入0；正式库前后均为626公司、1252证据、0审核、0成员、0评估。历史截点不是当前资格，逐年金额审核要求意味着仅有结构化数值不会自动产生通过或失败；不采用旧试算469/109。未推送、合并dev、发布成员或开启调度。

### 成交额与分红两公司隔离验证（2026-11-02）

用户确认两公司、最多20次含重试请求的隔离验证；不修改正式业务库。样本长江电力600900、招商银行600036；研究截点2026-11-02盘中，日历缓存抓取于2026-10-04，60个已收盘日为2026-07-31至2026-10-30。两家新浪原始日线各完整60日，抓取于11月2日；仅验证样本，不推断626家覆盖。

实际网络共14次，均HTTP 200，未触发429；正式radar.db前后SHA256一致。巨潮两词查询均完成，原始招商银行“利润分配”11条返回包含5条标题为“分红派息实施公告”的实施公告，原标题过滤漏掉。用实际标题先红后修，增加已返回标题识别，不增加查询词、不将查询范围完成当作年度分红完整性。离线重放缓存网络0，长江电力发现6条、招商银行5条。每家抽3份实施公告归档；长江电力3份匹配既有模板，产生7条待审核建议；招商银行3份PDF成功归档但提取器不支持其正文标题模板，均待核验。该样本明确区分A/H股，不能凭A股公告补造全股类完整性；本次不扩展金额提取口径。审核包两家公司均未完成支付率研究，缺股类范围及三年完整性证据，不形成资格结论。

临时完整响应与摘要在 `tmp/turnover-dividend-pilot-20261102/`，脚本及日志位于 `tmp/turnover-dividend-pilot*.py` 和 `.log`；原结果、离线复算和银行补充分别为summary.json、discovery-replay.json、bank-followup.json。未进行626家采集、未自动确认事实、发布成员或启用调度。标题回归使用真实招商银行公告标题；相关50项测试、Ruff及定向Pyright通过。后续批量接线、银行模板及全股类完整性审核需形成具体方案并确认。

### 已确认：A股分红证据口径与提取（2026-11-02）

用户明确仅核验A股分红，不寻找或汇总H股；红利分支最近三个完整财政年度各有已实施A股现金分红，A股覆盖截至评估截点须有审核依据。A股金额不表述为全股类支付率，支付率规则仍未配置。

1. 从已归档巨潮招商银行PDF裁切可复核样本，记录来源、日期、哈希和裁切；先写真实年度/半年度、约数、A/H、页码、更正冲突及原旧格式失效测试，保留失败。
2. 增加分红派息实施模板，仅生成A股待审建议：保留明确派息日期与原文、近似金额及单位；近似值不进入精确金额字段、不能自动确认。更正、冲突、未知股类或日期均待核验。
3. 红利分支使用明确A股范围与A股完整性审核事实，旧全股类总额不能直接冒充A股数据；提升风格版本及归档重评。审核包明确A股覆盖，不改变尚未确认的支付率研究口径或正式成员。
4. 离线验证现有真实样本、边界及归档；小批量真实验证在独立目录、限制请求并保留来源及抓取日期，不写业务库。范围门禁、审查后本地提交；不自动发布或调度。

样本来源：巨潮招商银行2025年度、2025半年度、2024年度A股实施公告，分别披露2026-07-04、2026-01-10、2025-07-04，2026-11-02采样；PDF地址分别为 https://static.cninfo.com.cn/finalpage/2026-07-04/1225409111.PDF 、 https://static.cninfo.com.cn/finalpage/2026-01-10/1224927640.PDF 、 https://static.cninfo.com.cn/finalpage/2025-07-04/1224071313.PDF ；SHA256分别为 `280660e578682efc2232e3420d270f9faeb167081d636001504ebbddb5eaf7ac`、`2763d93e08cbc703ccfea62a5bbf4d459d87042e779fb6bb229bcc8fd921a37c`、`83ce0619274beb6fba79a5093722aa19d93f10cb54e2b1ba13926eda1fd152ea`。文本由PDF提取并裁切至相关日期表，保留原行、页码和空白；完整PDF留在tmp。

#### A股分红证据验证

红利分支只接受`scope=a_share`且`a_share_coverage_complete=true`的有效审核事实；原`company_all_shares`金额不能证明A股金额而待核验，风格版本v6使旧归档重评。公司市值的全股类口径不变。支付率研究未修改，未把A股金额与公司总利润作为正式支付率。

新模板限定有明确A股分红派息标题和相应分配章节；近似金额仅保存数值、单位及币种供审核，`amount_yuan`为空，保持`pending_verification`，不产生全股类派息建议；更正及冲突保持待核验。三份完整招商银行PDF离线重放识别A股及派息日期，分别保留约206.91、208.97、412.58亿元，精确金额为空；原文和摘要见前述样本记录。初始5项测试按预期失败；一次冲突测试替换未匹配原文空格造成误报，修正后相关测试通过。

2026-11-02小批量真实验证招商银行600036与美的000333，查询各发现5条、每家抽2份归档；招商两份保留A股近似金额待核验，美的两份匹配原有模板；请求10次、正式radar.db前后SHA一致。原始产物及日志`tmp/dividend-a-live-20261102/`、`tmp/dividend-a-live-20261102.log`；均为抽样，不证明年度覆盖。未查询或汇总H股，未自动确认、发布、启动定时任务或采集578家。

最终暂存门禁 `.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff、Pyright零错误零警告、数据入口通过，pytest 366通过，日志`tmp/dividend-a-staged-20261102.log`。主会话复核旧口径失效、A股约数与审核状态，无独立代理审查；无UI代码修改。暂存门禁后仅补本记录并复核暂存差异。

#### A股多样本补充与提取修正

新增中国神华601088及中国平安601318各2份真实公告抽样，11月2日网络10次、正式radar.db前后摘要一致；不单独查询H股。初始中国平安两份因“A股共计派发现金红利”未识别而仅生成全股类待审建议。核对公告原文后新增该明确A股金额表述，优先识别A股股本；存在H股但A股股本缺失时不以公司总股本代替。仅输出A股事件建议，不输出全股类年度合计；不能确认股类时保留原因、不输出伪A股建议。两份真实样本先红，修复后完整PDF零网络重放：中国平安分别识别A股10446863781.34元、18655113895.25元及对应支付日；中国神华两份保持通过。匹配模板仅代表可供审核，不代表年度完整性或正式入池。

中国平安样本2026半年度及2025年度公告分别为 https://static.cninfo.com.cn/finalpage/2026-09-03/1225543317.PDF 和 https://static.cninfo.com.cn/finalpage/2026-06-02/1225342200.PDF，披露日2026-09-03、2026-06-02，采样日2026-11-02；PDF SHA256分别为`ac2b7a1be69bb445aab05b358e030c7725971d68f9d621adbe1b57b4945f1c93`及`942a48bb55d4edf844f65619ea745af01b922e11bc5eb35479b7f4ec1fa2650d`，裁切文本至相关日期表。完整响应`tmp/dividend-a-live-extra-20261102/`，复读`extraction-replay-a-only.json`；不代表578家覆盖。

补充修正暂存门禁：Ruff、Pyright零错误零警告、数据入口及154项测试通过，日志`tmp/dividend-a-followup-staged.log`；验后仅补本行及暂存差异检查。

### 已确认：删除股票池PDF命令并接通结构化分红（2026-11-02）

1. 以此前双公司真实接口响应为依据建立最小静态样本，先编写分红归一化回归：三年按报告期、派息日在截点前、A股每10股派息正值；预案、缺失、未来、重复、跨源冲突及报告期歧义保持待核验。先红后绿，保留来源日期与摘要。
2. 新增逐公司结构化分红采集、缓存和`dividend_review`任务，冻结范围、断点、跨池复用；原文与请求参数绑定并校验，重试预算、429和最新失败阻断旧结果。CLI/API调用与恢复沿用任务机制，旧PDF专用命令直接删除，保留历史数据及项目其他pypdf用户。
3. 增加分红只读来源及逐年状态，接详情、红利分支、关注草案与评估来源复读。结构化数值与已审核人工事实冲突待核验；旧审核记录不得被伪装成新来源。财务与成长复用现有实现，现金/身份/治理/规则不放宽。
4. 覆盖坏任务、缓存损坏、复读与旧档案失效、范围/两池、失败和恢复，先离线核对后两家公司最多30次真实请求；无全池、无发布、无调度。若变更前端则遵守真实服务、浏览器和发送响应验收；范围门禁、审查后整项本地提交。

#### 来源样本与验证限制

2026-11-02东方财富原始响应裁取600900与600036各5条、覆盖2023至2026报告期；完整响应摘要依次为`2c3e1eb539a0b7318915092dd4158339ef04554dfb97e7c88d79da0d16775cf1`及`1b37f64aa865413f0d0350ba20e7953a674139cfa1afbaac73424571ed3e024e`，完整原文保留在`tmp/dividend-raw-probe-20261102.json`。巨潮曾通过现有SDK返回两公司DataFrame，但原始响应未保存；本阶段受控原文探测连续连接中断或超时，因此不能确认其公司代码字段，按身份缺失处理为待核验，不以DataFrame替代原文。历史截点事后抓取只作参考，两源实际抓取日期分别保存；无法对应的记录不计通过。

用户要求本阶段先不进行真实服务及浏览器验收；该验收尚未执行，最终交付前须补做。未进行全池采集、发布或调度。

范围门禁`verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`：Ruff、Pyright、数据入口通过，pytest 811通过、1跳过；随后收紧逐年状态，相关分红、风格与接口测试106通过，相关Ruff和Pyright通过。巨潮原文探测仍未跑通，真实来源身份字段和双来源端到端结果尚待验证；不能据此声称分红接口已在真实数据完成替代。

### 已确认：候选成交额资料接线

1. 测试先行：固定60个已收盘交易日，验证完整原文、截点、公司身份、来源摘要、缺日和新任务失败不回退；低于1亿元仅作红利提醒及成长观察，不改变共同资格。
2. 复用新浪未复权日线采集，新增冻结候选范围的手动成交额任务，逐公司保存可复读原文和窗口，支持续跑、两池缓存复用及429停止来源；不修改行业或正式成员。
3. 详情与评估重新验证来源和市场窗口，使用现有条件区域；资料历史或冲突待核验。评估和草案绑定来源，变更使旧档案失效。
4. 离线测试及既有两公司样本复算，按确认预算小规模验证分红接口；不进行全池采集、定时、发布或本阶段浏览器验收。范围门禁、审查及本地提交。

两家新浪原文离线重算各有60日，窗口为2026-07-31至2026-10-30，实际抓取均为2026-11-02；不代表其他候选完整。巨潮本轮各尝试一次：600900返回26条原始记录但逐条无证券代码，600036连接中断。600900完整原文SHA256为`1059374a5f6dd42e93e9bdabca5776e08d15e6181cc66fe0c65cc6da144dd667`，测试资源裁取2023—2025年5条。其2025-01-16公告在巨潮标为2024三季报，东方财富标为2024半年报；保持冲突待核验，不能通过请求代码猜测原文身份。该探测没有写正式业务库。

招商银行依已确认预算另按3秒和10秒退避各重试一次，均连接中断；本轮共3次，停止继续请求。

改动范围门禁Ruff、Pyright、数据入口通过，pytest 852通过、2跳过；之后修正命令行默认日历回退并补测，成交额、任务执行器、命令及风格测试102通过，相关Ruff和Pyright通过。来源损坏、后续新任务未处理、交易日历变化及归档失效保持待核验。真实服务和浏览器验收遵照用户要求暂缓；未采集全池或发布成员。

### 已确认：按需人工审核资料包

1. 仅按明确指定的公司生成只读资料包，不批量拉取或向模型传入626家详情；复用现有详情的来源复读、规则计算与revision校验。
2. 测试先行，覆盖六项审核状态、已审核事实和来源日期、历史/缺失、公司不存在、来源冲突及只读性；不将名录类型、行业或财务数值提升为审核结论。
3. 新增单公司只读接口，投影证券身份、审计、治理、龙头、财务质量、现金专项及既有相关来源摘要；不调用AI、外部接口或新增页面，不生成审核或成员。
4. 以隔离合成资料及现有缓存离线验证，按范围门禁和代码审查后本地提交；不运行全池采集或本阶段浏览器验收。七条风格规则仍保持未配置。

2026-11-02对本地长江电力600900执行单公司零网络只读验证：六项审核条件均可投影，上市来源为historical，财务任务version_mismatch，行业为local_reference；不把历史或不兼容来源升级为当前审核。正式radar.db前后SHA256一致，正式成员发布数0。历史626家缺项仅整理为`tmp/manual-review-queue-historical-20261018.json`，413家无历史共同硬失败、213家有历史失败；它不是当前名单或当前资格。

提交前范围门禁：Ruff、Pyright、数据入口通过，pytest 227通过；额外相关准入与风格测试合计171通过。未新增页面，真实浏览器验收按用户要求暂缓。另基于原2026-10-18成长回放，零网络汇总TTM营收/扣非同比阈值0%/0%、10%/15%、20%/25%的历史情景；同时要求最近两个独立季度的两项同比均正，分别173、118、94家达到，均有110家不可判定。只比较历史研究参考，不作为当前资格或已确认正式规则；完整摘要`tmp/growth-threshold-historical-summary.json`。

### 当前两公司隔离复核（2026-11-02）

22:04—22:09在独立业务库副本对长江电力600900及招商银行600036试跑，网络请求22次，未超过30次预算；复读0次网络，正式radar.db前后SHA256一致。两家公司财务任务8项盈利数值均通过；2026-08-03至2026-11-02的60个已收盘市场交易日日均原始成交额分别约29.02亿元及30.91亿元。2026-11-02供应商总市值和60日均值均达到已确认数值线，分别约7218.12/7000.64亿元及10889.93/10288.23亿元；仍须独立核验其他条件。该次不请求全市场上市名录，上市来源保留2026-10-18历史参考，年限与六项人工事实均待核验。分红任务两家均partial：巨潮响应未含证券代码，无法验证归属，不伪造审核或回退旧结果；已确认的财务数值及流动性不是正式入池结论。原候选范围日期2026-10-16，399006快照2026-09-30，不当作当前指数成分。完整隔离产物`tmp/two-company-current-0daf01f88c4e499c8d208d040b4c32ee/report-current.json`，正式成员发布0。

试跑发现相对状态目录导致基础资料任务市值来源复读失败，已先用测试复现并将市值读取入口规范化为绝对路径；绝对和相对路径离线复读均为current，不修改行业映射或行业更新。相关测试26通过、1跳过，Ruff/Pyright通过。真实浏览器验收仍按用户要求暂缓。

修复暂存范围门禁：Ruff、Pyright、数据入口通过，pytest 97通过、1跳过；无全量门禁，未执行正式服务和浏览器验收。

#### 分红身份与报告期补充调查（2026-11-02）

巨潮历史分红原文长江电力26条、招商银行27条均无证券代码；分别留存原文摘要`1059374a5f6dd42e93e9bdabca5776e08d15e6181cc66fe0c65cc6da144dd667`、`c3438660d21fca2852c27c5a57c026b09bf1482ef2072322793f1d8b234f6ebb`。此前巨潮公告查询缓存包含证券代码、组织代码、公告日及标题，不需读取PDF即可获得该元数据；结合东方财富现有记录，样本近三年9笔均可按公司、公告日和金额一一对应。长江电力2025-01-16公告标题为“2024年中期权益分派实施公告”，但巨潮历史分红记录标为2024三季报、东方财富报告期为2024-06-30；有冲突仍按现行规则待核验，不自动选优。

东方财富公开页面`https://emweb.securities.eastmoney.com/PC_HSF10/BonusFinancing/Index?code=SH600900&type=web`的模板使用`PAY_CASH_DATE`，其页面请求`BonusFinancing/PageAjax?code=SH600900`。2026-11-02 22:42两家公司响应均有证券代码、方案进度、派息日及每10股方案，每家只返回10行；完整响应摘要分别为`8818758b46779893eaeda7ece64fad1a310eb653a5fade6058d8b4f90d00197d`、`94e8f6dbf9b40158972a386e0207630ef5c8d5ef0ad51c34634af6e3656367eb`。与已有报告期接口及巨潮公告标题离线对照，9笔均唯一匹配；10条截断无法证明没有其他事件，两种东方财富接口也不构成两个独立数据供应商。接口身份与完整性、报告期冲突处置是否改变原已确认设计须另行确认；本次没有将样本转为通过，也未读取PDF。研究摘要`tmp/dividend-pageajax-comparison.json`。

修复同一事项的失败追溯：HTTP成功但来源身份或结构无效时保留原始响应、请求、抓取时间、摘要及具体诊断在失败产物，不写成功缓存，也不对确定性结构错误重复请求；缓存写入失败不能标为完成。来源及规则仍保持待核验。

验证：分红、任务执行器及接口相关测试62通过；暂存范围门禁Ruff、Pyright、数据入口通过，pytest 25通过。未开展全池采集或真实浏览器验收。

#### 分红来源自动替换实施计划（2026-11-03，用户已确认）

1. 先增加单来源成功、首选不可用后切换、两者均不完整、代码冲突与归档失效的测试。
2. 东方财富以实施记录接口与同供应方年度接口组成一个来源；只在必要字段可唯一对应时使用。巨潮作为备用来源，明确记录请求代码绑定，不伪造响应中的证券代码。
3. 首选来源足够时不请求备用来源；失败或缺失时切换，两个来源都不能证明完整三年时保持待核验，不以缺失推断零分红。
4. 升级任务版本，保存请求身份、原文、摘要、实际日期和选定来源，复读时重新校验，旧任务不冒充新结果。
5. 执行相关范围测试与离线样本复核，审查变更并按完整事项本地提交；不发布成员，也不启动定时任务。真实服务和浏览器验收按用户要求延后。

2026-11-03 13:09—13:11 在独立库副本复核 600900、600036：截至 11 月 3 日评估，60 个完整交易日为 2026-08-03 至 2026-11-02。财务、成交额、分红任务均完成，16 次请求；两家公司各 8 项盈利数值通过，成交额参考通过，东方财富分红来源同截点复核通过并进入红利条件。六类人工依据、当期上市名录及当期市值仍待核验，两家公司整体待核验，不代表正式入池。随后隔离模拟东方财富不可用，巨潮实际请求两次均成功，两个分红任务完成且以 request_code 明示身份绑定。两项试跑正式库前后 SHA256 均一致，正式成员发布 0；历史候选范围不代表当前成分。完整结果分别位于 `tmp/two-company-failover-20261103-f4082a5c644b4afda691dd82a310391d/report.json` 和 `tmp/dividend-live-fallback-20261103-f979cb2f69af4257b00a692658ffa93e/report.json`。备用试跑临时脚本首次因 Windows 子进程入口未加保护失败，修正后重跑；首次失败不计为成功验证。

2026-11-03 当前范围与基础资料隔离批次：五个官方指数均完整，四个中证指数快照为 2026-11-02、399006 快照为 2026-10-30；两池去重 626 家（红利 578、成长 548），各池成员与原范围相同。上市名录及 626 家市值任务均完成，市值接口 626 次请求、429 为 0，正式库摘要不变。交易窗口为 2026-08-03 至 2026-11-02；离线复读无失效，上市年限通过/未达 618/8，当前市值 507/119，60 日均值 531/95；这些只是数值条件，不代表正式入池。结果及后续批次产物放在 `tmp/current-scope-20261103-2d80f0deef8e4dee9f83789fcb785fd7/`，成交额、财务、分红及全池评估完成状态以各自产物为准，不把运行中数据描述为已完成。

2026-11-03 离线核对当前候选库：六类人工条件对应的规范证据记录及审核记录均为 0，现有研究线索不能视为已审核事实。复读两公司同日财务任务现金流原文，2023—2025 年度记录已包含 `OPINION_TYPE`/`OSOPINION_TYPE` 字段，证券代码、公告日、请求、抓取时间与原文摘要可追溯；部分附加意见字段为空。该样本仅证明可复用已有原文作为审计审核线索，不证明 626 家覆盖、接口字段含义完整或审计／治理条件通过；后续先按已采集任务统计缺口，不因重复取数引入 AI 审核或额外批量采集。

#### 当前批次并行调查：人工依据与未配置规则（2026-11-03）

仅依据现有代码、当前资料与历史设计梳理；不新增业务规则、审核决定或网络采集。当前范围内六类对应规范证据均无记录，人工事实有效性仍按来源、披露日期、审核人与90天期限验证。批量财务/分红/成交额尚在运行，以下字段覆盖不能推断全部公司完成情况。

| 人工条件 | 可复用线索 | 仍需审核 |
|---|---|---|
| 证券身份 | 同截点官方上市名录含代码、名称、上市日及交易所 | 沪深普通A股身份和普通企业/银行/证券/保险分类；财务接口`companyType`只是线索，不自动等于已审核发行人类型 |
| 审计 | 已存年度现金流原文含`OPINION_TYPE`、`OSOPINION_TYPE`；两公司样本三年可见主要意见字段 | 原文身份、报告期、公告/修订日期和字段含义；非标准或未知专项核验，标准意见不覆盖内控、处罚及重大事项 |
| 治理 | 当前候选与公司资料可作为定位线索 | 风险警示、重大造假、资金占用、处罚等有时效的可追溯证据；不能由财务数值或行业分类推断 |
| 龙头 | 本地行业表及财务规模线索 | 主营业务、上市主体与行业边界，以及市场份额/竞争地位的证据；本地行业表无逐行生效日，市值排名和指数成分不证明龙头，不更新行业表 |
| 财务质量 | 财务原文、盈利数值、现金比率研究 | 明确发行人类型和对应行业适用性；银行资产质量/拨备/资本、证券净资本/收入结构、保险偿付能力/业务质量等专项不能用普通企业指标代替 |
| 现金专项 | 三年经营现金流与归母利润及来源 | 按发行人类型核验资金来源、支出和偿债或金融资本影响；现金比率达线不自动等于专项通过 |

现有单公司只读资料包投影以上六项及资料来源；显式添加证据命令要求公司、类型、原文来源、披露日、摘要和版本，单条审核需审核人和理由。现有页面可对已存在证据提交审核，资料包与审核均不会自动建立证据或发布成员；资料收集范围、授权审核人和具体获取方案仍须依据实际界面与用户授权确认，不伪造人工决定。可先以本次三项基础数值均达到的495家公司作为后续审核排序线索，其他公司保留记录；这是2026-11-03截点的优先级，不是通过共同条件或正式入池。

| 未配置规则 | 已有资料或研究 | 尚需决定或补足 |
|---|---|---|
| 红利支付率 | 已实施A股每股分红、三年归母利润及历史研究算法 | 支付率定义和阈值未确认；A股金额与全公司归母利润不能混称全股类支付率，实际派息金额与覆盖也未完整证明；用户明确不需寻找或汇总H股 |
| 红利股息率 | A股实施记录与供应商收盘价；本次626条收盘价字段均为正 | 12个月窗口、股份事件调整、完整覆盖及3%仅为建议；未确认准入/机会用途 |
| 价值估值 | 本次供应商60日原文含收盘价、PE_TTM和PB_MRQ；正值字段分别为626、558、626家 | 行业适用指标、比较口径、历史区间及门槛未确认；现有60日不构成建议的3年以上/600点历史 |
| 成长营收 | 同财务任务可重算TTM及近两独立季度同比；10%/季度为已确认研究参考 | 是否作为正式风格规则及最终阈值未确认；基数非正或不可比不填同比 |
| 成长扣非 | 同任务可重算TTM及近两独立季度；15%/季度为已确认研究参考，改善路径另有研究口径 | 是否正式启用、低基数/修订/一次性因素及季度边界处理需确认；不以参考达线替代质量审核 |
| 成长估值 | 同上PE/PB现值与有限历史线索 | 行业适用指标、估值分位与阈值未确认；60日数据不足以证明多年分位 |
| 成长趋势 | 正在采集未复权日线和成交额 | 60日数据不必然覆盖“20日前MA60”所需更长窗口；相对收益须核对含分红个股收益与全收益基准，公司行为及阈值未确认 |

以上不需人工逐家阅读原始资料或调用AI：先程序化汇总来源覆盖与例外，再按公司、证据类型、失败原因生成可追溯资料包，由授权审核人逐项决定。当前尚未接通自动生成六类规范事实；无需因为审计字段存在就额外请求相同财务数据，是否将线索投影进资料包应先核对原文归属/日期/版本及不自动审核的具体方案。

#### 自动研究候选与按需解释实施计划（2026-11-03，用户已确认）

1. 测试先行：用合成评估覆盖通过、明确失败、缺失和冲突的研究摘要；列表仅显示所选池范围，候选可查看但不能据此发布；损坏及历史来源不冒充当前结果。
2. 抽取只读单公司评估与研究摘要，列表按当前页计算并共享来源校验；详情展示本池已满足、未满足、待核验及未配置项，保留各来源原始日期和原因，不创建审核记录或成员。
3. 在现有详情/资料区域投影公司身份、行业、财务和三年审计意见等已存线索；原始响应摘要、公司、期间和抓取/披露时间须核实，缺失及冲突不升级事实。
4. 新增仅用户主动请求的单公司AI解释：复用现有模型配置，限制输入/输出及耗时；只传受控摘要，不自行联网、不读取密钥、不写业务库；模型未配置、失败及恶意内容均可见降级，不产生审核或资格决定。
5. 复用现有列表与详情结构，添加研究摘要及按需解释控件；已有审核、来源和草案不移除；不新增页面，不触发后台批量AI。
6. 验证单元/API/前端相关范围、来源失效与无意外网络；审查归档与正式发布阻断；真实服务及浏览器验收按用户要求留至最终验收，完成独立事项后本地提交。

研究候选实现：候选列表按池先显示范围、并发上限2逐行读取研究摘要，详情按结果分类并显示审计原文线索，AI仅由详情按钮主动请求，输入上限12000字符、输出预算800 token、失败不写业务库。两公司既有财务原文离线样本均可按年度复读；来源错误、超时及不完整保持待核验。隔离真实保存数据的候选列表接口约0.33秒，逐行摘要独立加载；浏览和批量任务不调用模型。相关模块136项测试及单文件Ruff/Pyright通过。真实服务与浏览器验收按用户要求延后，现有采集任务继续运行。

提交前按变更范围验证：Ruff、Pyright、数据入口检查通过，pytest 632项通过；随后补充来源显式报错边界，先复现再修复，受影响44项测试和单文件检查再次通过。模型工厂使用应用现有运行时实例，缺失时不初始化/迁移配置；解释期间来源变化返回冲突。真实服务和浏览器验收仍按用户要求留到最终阶段。

#### 盘后窗口与人工线索抽查（2026-11-03）

不修改正式业务库或行业映射。用已保存的600900、600036财务、官方名录、本地行业和单公司资料包验证：两家公司2023—2025年审计意见字段均可从原文复读，附加意见、公告和原文摘要按实际记录保留；官方上市年限数值条件通过，行业只作为本地分类参考，发行人类型仍未审核，六类人工条件均保持待核验。可自动整理不等于人工审核或正式资格；未调用AI，也未为该项再下载财务。

15:00后交易日历窗口由2026-08-03—11-02变为2026-08-04—11-03。冻结的626家公司中622家有成功成交额原文、4家无成功原文；其中17家有盘后抓取但全部离线解码缺11月3日，其余605家仅有盘前原文。因而现有成交额原文均不能证明新60日窗口完整，使用原入口更新需覆盖626家，原先4家缺口不保证更新后消失。同期626家市值原文均为盘前抓取且窗口止于11月2日，也需更新以作收盘后当前市值判断；上市名录不因收盘自动失效。自动复读对失效窗口保留待核验，不能把盘前结果冒充盘后资格。

17:08—17:09仅对600900、600036各发起一次新浪日线请求，HTTP均200且新60日窗口完整；样本不能保证全部626家覆盖。来源限流、停牌或缺日仍须逐公司记录。详细离线统计、逐公司窗口清单与两次样本原文留在`tmp/current-scope-20261103-2d80f0deef8e4dee9f83789fcb785fd7/`。分红及后续原队列继续执行，未插入会改变队列顺序的补采任务；如后续刷新，须保留实际抓取与交易日期，重复两池共用原文，不修改行业或正式成员。

#### 七条风格规则讨论稿（2026-11-03，未确认、未启用）

已确认基础条件、现金流0.8的分池作用、三年A股实施分红和流动性作用保持原样。本节只是整理可讨论方案；不自动改变正式资格，不采集H股或做A/H汇总。研究数据按实际抓取日、财政期间及行情日分别标注。

| 池/规则 | 建议处理 | 数据及待决点 |
|---|---|---|
| 红利：支付率 | 当前先标未配置，资料足够后并列显示每年支付率、三年年度均值和最近一年值；先不把单年超100%自动排除 | 已有归母利润及A股每股派息，但不具备完整实际派息总额；A股派息总额不可直接除以全公司归母利润并称公司支付率。若研究每股口径，须先证明每股利润、每股分红的年度、股份基数、权利和税前口径可比；不寻求H股数据。三年累计比率与年度均值不同，(0,100%]只是历史草案 |
| 红利：股息率 | 建议作为机会提示，不作为入池硬门槛；历史草案3%可用于对照，未获正式确认 | 建议以截点前12个月已实施A股每股现金分红/同截点A股收盘价研究；须核验各次分红覆盖和送转等股本变化调整，不把缺项当零或把跨期、税后与税前混用 |
| 红利：价值估值 | 建议区分公司风格与当下买入机会；历史自身40%分位仅供讨论 | 现有窗口有收盘价、PE_TTM、PB_MRQ，但只有60日，不足以计算建议的至少3年/600有效点分位；金融及周期企业的指标适用性需分别核对，负PE不视为低估 |
| 成长：收入增长 | 已确认的研究参考为TTM营收同比≥10%，且最近两个独立季度收入同比均>0；是否纳入正式风格资格待确认 | 已复用财务原文；基数非正、披露晚于截点、币种不一致或修订冲突待核验，不自动将未知判失败 |
| 成长：扣非增长 | 已确认的研究参考为TTM扣非归母同比≥15%，且最近两个独立季度扣非同比均>0；是否纳入正式风格资格待确认 | 与收入增长共同形成持续成长研究路径；经营改善路径另按两季度收入/扣非同比均正及扣非利润率同比改善研究，不取消共同连续盈利要求 |
| 成长：估值 | 建议作为机会判断，不因暂时价格高就取消已核实公司资格；自身估值分位≤80%只是历史建议 | 需决定适用的PE/PB等指标、行业比较、有效历史与上限；60日数据不足以计算多年分位，非正或缺失指标不能代入零 |
| 成长：趋势 | 建议作为机会判断，不直接决定成员资格；MA20/MA60、偏离15%及相对收益线均为待确认建议 | MA60与20日前MA60比较至少需80个相应交易日且公司行为口径一致；相对收益只有个股含分红收益与沪深300全收益基准可比时才判断，缺失保留待核验，不混用复权口径 |

原设计建议的估值历史是5年、至少3年/600个有效点及正值覆盖≥95%；这些均待确认。2026-11-03盘前保存的626家公司原文仅作字段覆盖检查：前一收盘日PE_TTM为正558家、非正68家，PB_MRQ为正626家；不代表当日收盘估值、历史分位或资格。已实施分红接口缺年不代表未分红；当前批次仍在运行，完成后复核字段覆盖和待核验原因。

建议推进顺序：先利用已接线财务任务完成收入/扣非研究结果与异常汇总；并行整理A股分红事件、每股数据及公司行为的覆盖；之后在不混用估值日期与股类的前提下讨论支付率、股息率、估值与趋势是否作为风格资格或机会参考。候选可继续查看；任何规则正式启用前，须就具体口径形成完整方案并确认，且不自动发布成员。

2026-11-03 17:21：在两池财务任务结束后，对当前去重626家公司逐一复读各自最新适用财务任务及主要指标原文，校验摘要、公司/报告期/公告/修订日期，前后核对财务任务和指数范围快照不变；不发网络请求、不调用模型、不写业务库。626家公司成长主要指标来源均可读取，但财务任务各池各有1项整体待补，不能表述为全部财务条件完成。持续成长参考全范围达到119、未达到398、待核验109；经营改善达到126、未达到405、待核验95，两路径至少一项达到158、均达到87。成长池548家持续成长119/329/100，经营改善113/349/86，至少一项达到145、均达到87；红利池578家分别109/374/95及116/379/83，至少一项达到146、均达到79。各路径与两池重叠，数字不得相加为公司数；全部只是2026-11-03截点的研究参考，不代表正式风格资格或入池。

全范围持续成长待核验中，TTM扣非同比缺失或不可比94项、较早季度扣非同比81项、最新季度扣非同比65项；同一公司可同时出现多个缺项，原因可能包括非正基数、修订冲突或资料缺失，不能把这些计数相加，也不能笼统认定全部缺失。逐公司数值、原因、抓取时间和原文摘要在`tmp/current-scope-20261103-2d80f0deef8e4dee9f83789fcb785fd7/growth-current-research-details.csv`，汇总及路径重叠在同目录`growth-current-research-summary.json`；分红任务仍独立运行。

#### 分红待核验来源离线分类（2026-11-03）

分红任务已结束，逐项复读两池全部236条partial任务的不可变产物、来源身份和原文摘要，不请求网络、不创建审核或成员。红利池111项、成长池125项，涉及131家不同公司，其中105家公司在两个池的原始任务中均待核验；这是两次任务的并集，不等于最新来源选择后的当前待核验总数。所有236项都至少有一个可独立复读来源，但没有可独立证明最近三个完整年度均有正向实施记录的完整依据；不能因此断言公司未分红。

红利池111项中东方财富页面来源均可复读，年度接口102项完整、9项显式返回失败且result为空；巨潮101项完整，8项请求异常、2项HTTP 403。成长池125项中东方财富年度接口111项完整、14项显式返回失败且result为空；巨潮122项完整、3项请求异常。接口失败与年度依据不足可同时存在，不能将这些类别简单相加，也不能把明确失败的空响应视为未分红。明细和来源/缺失年度位于`tmp/current-scope-20261103-2d80f0deef8e4dee9f83789fcb785fd7/dividend-pending-offline-details.csv`，汇总在同目录`dividend-pending-offline-summary.json`。后续先区分接口失败、缺少可确认事件和最新跨池结果，再决定少量重试或补证；不盲目重跑全池。

#### 针对性分红重试与盘后更新（2026-11-03）

复核两池任务发现巨潮请求异常涉及9家公司，其中6家已在另一池获得成功原文；另有2家公司出现HTTP 403，以及14家公司东方财富年度接口明确返回数据为空。只对仍缺跨池成功原文的3家巨潮请求异常各重试一次，3次HTTP 200：600161可由巨潮独立证明三年正向实施记录，600157与301611仍待核验。原来源和重试原文均保留，使用正常任务续跑入口在隔离库离线追加结果；先复读原有全部890项完成产物，续跑本身网络0，未改正式库或发布成员。未将空响应当作不分红，也未重复请求403来源。

重试后按每家公司最新适用任务和原文离线复读：626家中三年分红数值依据通过500、待核验126；红利池472/106，成长池424/124。两池与原始任务的待核验记录相互重叠；原`financial-dividend-summary.json`是重试前的历史快照，不是最新统计。最新汇总和明细分别为`tmp/current-scope-20261103-2d80f0deef8e4dee9f83789fcb785fd7/dividend-current-source-summary.json`与`dividend-current-source-details.csv`。财务原任务中的601018异常已被成长池同公司较新完整任务覆盖；688820最新任务虽采集完成，上年同期扣非字段不可用，TTM扣非仍待核验。

用户同意盘后数据更新后，先以600900预检市值新窗口成功；两家公司盘后成交额样本经摘要、身份、窗口和日期校验后放入隔离缓存。随后按单任务队列启动626家盘后市值及两池成交额更新，继续复用本地行业表与当日上市名录，不改正式库或发布成员；仅在任务结束后生成单独的盘后评估文件，不覆盖盘前历史汇总。阶段进度由同目录`postclose-refresh-progress.json`记录，完成与否以任务真实状态为准。

#### 盘后复核与研究关注清单（2026-11-03）

盘后基础资料与两池成交额任务均已结束，626家市值来源完成；成交额红利池574完成、4待核验，成长池544完成、4待核验，4家为相同公司。全范围离线复核626家、错误0、网络0、未写正式库或发布成员，窗口2026-08-04至2026-11-03；指数范围快照分别保留2026-11-02和399006的2026-10-30日期。详细结果及来源绑定见`tmp/current-scope-20261103-2d80f0deef8e4dee9f83789fcb785fd7/postclose-evaluation-summary.json`及对应明细。

两池缺失均为000831、002129、002155、601238；离线复读原文和摘要均吻合，前两家分别缺11月2日及3日，后二家分别缺5日及10日；来源缺日原因尚未证实，不能断言停牌或填零。缺日明细在同目录`turnover-four-offline-diagnostics.json`。成交额仍只作提醒或机会参考，不是共同资格线。

共同条件红利池197家有明确失败、381家待核验；成长池190家有明确失败、358家待核验。逐条件明细离线重算得到无共同硬失败而仍待核验的研究关注线索分别381、358家，去重399家，数字与复核汇总相符，附分红、成交额和成长参考状态；清单位于同目录`postclose-attention-research.csv`，不代表风格通过、已审核或正式入池。证券身份、审计、治理、行业地位、财务质量及现金专项仍无已审核规范事实；七条讨论规则仍未确认或启用。上市日期、财务来源、行业分类及研究参考不能自行升级为审核事实。

采集性能初步离线测量：两份新浪原文一次完整解码约1.1秒；成交额任务目前逐公司固定等待1秒，缓存命中后也等待，并在采集及产物验证时分别解码。子进程隔离有助于超时保护，优化须保留原文、身份、日期、摘要、窗口复核和失败隔离；对网络限速须作用于实际请求并跨公司共享，不凭本地小样本认定可安全提高供应商请求频率。两样本仅为定位线索，不作为全量耗时承诺。

#### 审计线索覆盖与例外分类（2026-11-03）

复用同一日已完成的全范围离线盘点，不重复采集：626家2023—2025年共1878条年度记录均有主要审计意见字段；其中1869条为“标准无保留意见”，8条为“带强调事项段的无保留意见”，1条为“保留意见”。主要意见不为“标准无保留意见”或`OSOPINION_TYPE`非空共涉及18条记录、11家公司，其中9家在盘后研究关注清单；`OSOPINION_TYPE`非空的9条值均为“标准无保留意见”，不能仅因字段非空判定异常或不通过，字段真实含义仍需核对。意见类型与附加字段可能重叠，不应直接相加为公司数。

统计只用于定位原始资料，不等于已审核事实、审计条件结论或正式入池；包括“标准无保留意见”在内均需保持审核状态与原文绑定，不由模型或脚本代替审核。结果及原始行日期、摘要分别见`tmp/current-scope-20261103-2d80f0deef8e4dee9f83789fcb785fd7/audit-clue-categories-summary.json`、`audit-clue-exceptions.csv`和`audit-clues-detail.csv`；分析网络0、未新增审核及成员。

#### 已确认第一阶段：复用证据线索与保持规则边界（2026-11-03）

用户确认：官方名录、财务和行业仅提供来源绑定线索；审计年度意见与报告期、公告日和摘要展示供核验，不自动审核或发布。治理、行业地位、财务质量、现金专项依据不足保持待核验。股息率及估值/趋势定位为机会参考，支付率暂未配置；既有收入10%和扣非15%及季度要求只作成长研究参考，不直接成为正式门槛。分红、现金比率、成交额已确认规则不变。

执行顺序：1）核对现有详情、研究参考及资料包，保留已实现能力；2）测试先行补充资料包对已校验审计线索的只读投影，检查公司、截点、未审核状态与深拷贝；不一致或缺失时降级，不能影响审核和资格；3）核对股息率、估值及趋势数据是否足以计算，未证明的口径继续待核验，不凭提示定位清空未配置规则；4）离线样本与范围测试、代码审查，记录结果并完整事项提交。此阶段不重复采集或调用AI，不修改行业表、正式成员，真实服务及浏览器验收按用户要求暂缓。

第一阶段核对发现审计意见、成长参考和六项状态已在详情呈现；补充资料包复用同一次已复读审计线索，仅接受匹配公司和截点、未审核且不启用正式筛选的结果，其余降级为不可用，历史状态保留。未改审核事实、规则列表或成员。现有分红来源证明年度实施记录，不足以证明近12个月事件全覆盖和股本调整；股息率不能据此计算。现有60日估值数据和不复权价格也不足以证明所需长期估值分位或可比趋势，继续未配置/待核验。

验证：项目工作树源码路径显式加入PYTHONPATH后，资料包、研究候选和管理接口测试42通过；相关Ruff和单文件Pyright通过。初次测试先后遇到源码路径未指向工作树及pytest临时目录父目录不存在，分别修正环境后重跑通过；未运行全量门禁，真实服务和浏览器验收仍按用户要求暂缓。

#### 已确认：合并推进证据与规则线索（2026-11-03）

用户确认作为一个事项推进：复用当前来源，少量探测公开名录和风险信息，只有确认主体、日期及覆盖后才接入；统一单公司展示证券、审计、治理、龙头、财务、现金、分红和成长资料，不批量AI、不自动审核；支付率、股息率、估值和趋势不足时不虚构结论；来源冲突失效保留待核验，不更新行业表或正式成员。

细化执行：1）盘点现有详情的来源绑定与复读、收集官方来源样本及覆盖限制；2）测试先行，将分红、成交额、成长、市值来源以原状态和真实日期纳入单公司资料包，验证公司/截点绑定、历史、缺失、冲突、深拷贝和不变更正式判断；已有审计、上市和现金线索继续保留；3）核查股息率、价格趋势及缺失行情是否满足口径，无法证明时明确待核验；4）针对改动跑相关测试、代码审查和隔离复读；来源未验证不接成正式判断，不暗示金融名录缺失意味着普通企业。真实服务及浏览器验收依用户要求延期，不改业务库、发布或调度。

合并事项阶段结果：单公司资料包现在复用同一评估中已绑定的分红、成交额、成长、市值来源，附状态、原始抓取日期、窗口及摘要；不同公司或截点的来源降级为不可用，历史不冒充当前，失败不携带通过结果。两家公司使用隔离库2026-11-03数据零网络复读，上市、财务、分红、成交额、成长和市值来源均为current，行业为local_reference，六类审核均待核验，不产生审核或成员。两家公司样本不能代表626家。

官方入口小样本只读查看：证监会证券公司名录页面提供2026年9月附件，上市主体名称与持牌法人、快照时效及完整性仍未验证；金融监管许可证公开查询页面要求验证码，不能假定可无人值守批量请求；上交所风险警示页面的列表依赖页面动态数据，历史日期、完整性和深交所覆盖尚待验证。未绕过验证码，不以查询空结果证明没有风险或发行人类型。上述均未接入正式评估，仍需可靠来源或明确替代方案。

验证：资料包、研究候选、结构化分红、成长来源和管理接口测试85通过；随后只增加成交窗口与抓取日期投影，资料包测试14通过，相关Ruff及单文件Pyright通过。首次组合测试因测试文件名错误未执行，已更正后通过。未运行全量门禁，真实服务及浏览器验收按用户要求暂缓。支付率、股息率、估值和趋势尚无足够可比数据，保持原状态；未完成对4家成交缺日原因的独立确认。

后续来源探针：证监会2026年9月证券公司名录附件仅一次HTTP 200，表格列为序号、公司名称、辖区，没有证券代码；未验证上市主体映射或11月完整性，不能仅按名称升级发行人类型。东方财富停复牌接口仅按4个日期请求各一次，响应均HTTP 200、单页、返回条数与总数一致，原文、摘要、真实抓取时间及请求日期保留于本次隔离目录；对照四家公司全部缺日均落在对应停牌记录的区间内，作为缺日原因线索，不能因此填零或自动通过流动性。接口查询历史日期时混入较晚停牌事件；两个记录的结束日期晚于2026-11-03研究截点，不能把预期或事后信息视作截点已知事实。应后续核对交易所公告、原文日期与历史可用性，当前来源和条件仍待核验。对照文件为`tmp/current-scope-20261103-2d80f0deef8e4dee9f83789fcb785fd7/suspension-missing-date-comparison.json`。

#### 统一来源继续核验（2026-11-03）

东方财富风险警示板公开接口先探测一页，源声明392条但只返回100条；随后依次请求4页，HTTP均200、各页声明总数392、合计392且代码和市场组合无重复，原文与摘要留于本次隔离目录。按证券代码和市场与固定626家公司离线匹配0家，但采集时间为2026-11-03盘后，动态分页、供应商板块定义、历史覆盖及交易所交叉核验尚未证明；未命中不能代表没有风险、没有处罚或治理已审核，也不改变正式资格。

证监会2026年9月证券公司名录附件单次HTTP 200，150家公司名称与注册地，原文摘要`5f3de07aee7cc2a7c8470cd24979caaf5247a3a035e5f1f5c23e0f2962cafacb`；复用已校验的11月3日沪深上市名录，626家均有公司全称，精确名称匹配2家：600030、601211。来源无证券代码，且快照与研究截点不同；匹配仅是待核验线索，未匹配不能视为普通企业或无金融业务，未新增已审核发行人类型。统计、真实抓取时间和来源在同目录`securities-roster-name-probe.json`、`risk-warning-probe-summary.json`，与原研究缺口合并的索引为`evidence-gap-unified-with-probes-20261103.csv`；以上不写业务库、不调用AI、不修改行业表或发布成员。

复读600900、600036已保存分红原文：年度接口分别25、26行，包含报告期及除权日；带派息日的同供应商页面接口各仅10行。该样本不证明近12个月事件覆盖、跨页完整性或送转调整；不能以缺失当零，也不能就此启用股息率。后续仍需证明单一来源的完整性及可比口径，并保留备用来源可替换；不做A/H汇总。

供应商类型字段离线覆盖及原文一致性：626家最新适用财务任务均为current，代码分布为3：32家、4：588家、1：2家、2：4家；626份原文长度与摘要匹配，复解析页面`hidctype`与派生代码均一致。证监会9月证券名录精确名称匹配的600030、601211均为代码1；600036、000001样本为代码3，601318为代码2，600900为代码4。样本相关性不证明供应商代码定义、金融许可证持有人即上市主体、类型覆盖完整或适用性已核验，不得自动生成已审核身份、现金专项或正式入池结论。结果及原文摘要在`tmp/current-scope-20261103-2d80f0deef8e4dee9f83789fcb785fd7/company-type-coverage-summary.json`和`company-type-raw-consistency.json`；统计和复核网络0、AI0、业务写入0。

已将现有条件、审计、分红、成长、风险板块及供应商类型代码合并到同目录`evidence-gap-unified-current-20261103.csv`；保留各自来源时点，未匹配风险或证券名录不表示无风险或非金融。两公司分红样本的近12个月年度接口各出现2个除权日，派息页面各返回10条且无完整性声明；不能据此断言全部事件齐备或公司行为口径可比。

备用分红来源样本：600161已保存巨潮原文声明count=total=27且实际27条，单条字段含F006D公告日、F020D除权日、F023D派息日、F012N每十股派息、F010N送股与F011N转增；字段名称对应本地AkShare适配器。该样本说明可研究用单一巨潮来源重建实施事件，但不证明所有股份变化类型完整、分红是否覆盖全部事件或每股基数可比，不能启用股息率；东方财富与巨潮仍是可替换来源，不强制互相验证。

#### 已确认：风险来源接线、分红覆盖与端到端复核

用户同意将风险警示与发行人类型线索接入可重复运行流程，接着处理分红实施事件/股本变化，并在隔离数据上验证采集、汇集、复核和续跑。风险名单只表达抓取时的观察命中，未命中不视为治理合规；供应商类型仅原始代码，不映射为已审核类型。实施顺序：1）测试先行，建立可重复采集及只读来源，验证分页/总数/唯一性、时间、原文摘要、失败、历史及跨公司身份；2）接现有公司资料区域，校验读取期间来源一致，不修改正式评估/成员；3）核对分红来源事件全覆盖、股本变化和日期，不能证明时不算股息率；4）覆盖范围测试及隔离全流程，真实服务、浏览器和全量检查在最终阶段按此前用户同意执行。任何正式规则或最终发布仍以已确认范围及另有授权为准。

#### 已确认七条规则及风险线索实现（2026-11-04）

按用户已确认用途调整：成长收入及扣非保留研究参考；支付率暂不启用；股息率、两池估值及成长趋势列为机会参考且数据不足时待核验。以上不再被错误归入未决定的正式入池规则；独立正式筛选开关仍关闭，所有条件通过且规则列表为空时新增成员仍被拒绝。风格版本升级至v9，旧评估档案须重评；未发布成员。

新增风险警示板按需采集命令和只读详情线索，保存分页原文、摘要、抓取日期；限制网络请求，遇错误失效，不以未命中说明无风险。系统文件锁防并发覆盖并允许异常退出后恢复，损坏、重复、代码市场不匹配及日期无效降级。初次实采因代理失败，11月4日交易时段又发现按实时涨跌幅分页导致重复，原文复读拒绝；改用代码排序并升级来源v2后，4页实采与复读成功，真实抓取时间11:29，600900未观察到、300010观察到，均只作线索。动态板块和分页不能证明无其他风险，旧v1快照不冒充新来源。

相关测试覆盖规则、档案、成员、风险来源和页面；内置浏览器在隔离服务真实查看平安银行详情，支付率、机会参考和11月4日风险观察显示正确，11月3日财务资料标历史、正式成员按钮禁用。列表并发读取最初触发409，延长读锁等待并补并发测试后复查未出现该错误。浏览器会话测试消息已发送并出现思考状态，但截至本记录尚未观察到最终响应，故不能宣称完整会话验收通过。未更改正式业务库或行业表，分红股息率所需完整性仍待核验，正式成员未发布。

#### 审计意见自动分类（进行中）

复用已校验财务原文对最近三个完整年度进行研究分类：来源须同截点、三年唯一、均有有效意见且未在截点后更新；三年主要意见均为标准无保留且附加意见字段为空时标记`standard_opinions`，存在其他主要意见标记`other_opinion`，缺失、重复、历史、截点后更新或附加字段含义未核实时保持`pending_verification`。分类仅为线索，不自动创建审核事实或修改正式条件；历史来源降级时重新计算分类。对2026-11-03隔离资料零网络复读626家，615三年主要意见标准且附加字段为空、5存在其他主要意见、6待核验；详细结果在`tmp/current-scope-20261103-2d80f0deef8e4dee9f83789fcb785fd7/audit-opinion-classification-summary.json`，不作为2026-11-04当前资格。

本轮相关准入、风格、归档、成员、风险、资料包及接口页面测试287通过，Ruff与单文件范围Pyright通过；内置浏览器通过隔离真实服务查看000001，2026-11-03财务资料在2026-11-04评估时正确标注历史、审计分类待核验、未启用正式成员。未测试真实聊天最终回复；用户已明确股票池不依赖聊天，不重复触发会话。6家分类待核验原因均包含非空附加意见字段，其实际含义尚未核实，不能仅按字段文本升级通过。证券身份、治理、龙头、财务质量及现金专项正式证据尚未完成，本阶段未改变正式判断。

#### 已确认：正式判断依据的自动化接线（进行中）

用户确认六类依据统一推进。先复用已保存财务任务，测试先行：绑定同公司、同截点及原文摘要的最近三个完整年度审计意见；三年均为标准无保留、附加字段无未核实内容、公告及更新日期有效时作为结构化审计条件依据；非标准、缺失、历史、来源错误或人工事实冲突时不自动判失败或回退旧来源。结果明确标示结构化来源，不创建人工审核或成员；来源变更或失效须使归档重评。证券身份须先同时证明普通A股与发行人类型；证券机构匹配只作线索，治理、龙头、财务质量和现金专项未证明时继续待核验。相关测试和隔离626家复读，保留研究截点，真实成员不发布。

审计结构化条件已接入共同判断：同公司、同截点、来源端点和摘要、证券代码、三个完整年度年报、公告与更新日期、抓取日期及请求参数均通过校验，且每年主要审计意见均为标准无保留、附加意见明确为空时，该条件可通过；其他意见、附加字段缺失或含义未核实、历史/损坏/请求不符均待核验。已审核人工失败与结构化通过冲突时保留双方待核验；来源失效不回退人工通过，也不创建人工审核记录或发布成员。财务来源将审计分类与规则版本一并绑定，原文损坏或最新任务变化使归档失效。沿用财务任务v3，不改变旧任务版本；新增审计规则版本绑定在投影中。

来源口径核查：东方财富已保存页面中`reportType=1, reportDateType=0`标为“按报告期”，不是据此推断发行人类型或声称合并口径；626家最近三个完整年度共1878条原文的`REPORT_TYPE`均为年报且`REPORT_DATE_NAME`均匹配年度，公告、更新和抓取日期关系无异常。只按年度审计意见字段判断，证券身份及其他条件仍独立。2026-11-03隔离任务零网络复算626家：审计条件615通过、11待核验；红利池567/11，成长池537/11，结果在`tmp/current-scope-20261103-2d80f0deef8e4dee9f83789fcb785fd7/audit-condition-offline-summary.json`及对应明细。两池存在重叠，结果不代表2026-11-04当前资格、其他条件通过或正式入池。

验证：审计合并、金融来源、原文解析、共同条件、风格、评估归档、成员保护、财务任务及管理接口共325项测试通过，Ruff和相关文件Pyright通过；独立复审发现的缺失附加字段、重复JSON、错误日期、请求证券及期间不符、来源失效与任务并发变化问题均已补测试。未重跑全量门禁，不修改行业映射、不创建审核记录或正式成员。

证券身份接线：复用同截点沪深官方名录原文，仅在公司代码、名称、市场、来源摘要和抓取日期一致时标示已核验普通A股的市场部分；发行人类型仍须独立有效依据，未知时整个证券条件保持待核验。抓取日期不等于研究截点的名录只能历史参考（包括上市年限）；来源冲突保留人工结果与名录结果，不凭供应商公司类型或行业映射猜测发行人类型。绑定现有上市资料任务和归档，不更改行业映射或成员。

2026-11-03隔离快照零网络复算626家：市场身份618已核验、8待核验；红利池570/8，成长池543/5；结果只证明市场身份，不证明发行人类型或其他资格，更不代表正式入池。审查后补充同日抓取、名称不匹配、错误公司、人工冲突及其他条件嵌套失败的保护。

#### 监管措施来源隔离核查（2026-11-06，进行中）

- 上交所科创板监管措施页面脚本使用 `BS_KCB_GGLL_NEW`、站点28和渠道10007—10010；2026-11-06隔离采集35页，各页声明总数523，合计523条，按文书ID、代码、措施类型和对象组合无重复。采集是动态列表一次快照，未证明历史完整性、撤销变更或全部沪深覆盖。与2026-11-03冻结626家代码匹配21家公司、41条，含监管工作函24、公开谴责2、监管警示8、通报批评4、监管关注3；不把未命中视为治理合格，也不将工作函等同处分。原始分页、URL、抓取时间、SHA及匹配明细在 `tmp/sse-measures-pilot-20261106/`。
- 抽取公开谴责、通报批评、监管警示三份PDF均HTTP 200，正文均明确对应上市公司及责任人；仅为样本，不代表其他记录均已验证。睿创微纳文书落款2022-10-10，列表时间2022-11-03，说明文书日期与列表日期不能混同；截至历史截点是否可见仍需独立核验。决定可能被变更或撤销，现有快照不能证明其最终状态。
- 上交所统一监管页面可选择主板及科创板，脚本同用查询标识，但站点参数与覆盖尚需验证；深交所、证监会及银行保险监管资料仍未完成。该探索未写业务库、未创建审核或正式成员。财务质量、现金专项、行业地位与发行人类型仍待适用主体和独立依据；本条不改变正式规则。

#### 正式依据继续核查（2026-11-06，进行中）

- 上交所统一上市公司监管页面站点28、板块参数为空时包含主板及科创板；2026-11-06按页面25条分页抓取，223页声明总数均为5571，合计5571条，按文书ID、代码、措施类型和对象组合无重复。对2026-11-03冻结候选代码匹配192家公司；原文分页、请求、时间、SHA与逐条匹配存于 `tmp/sse-all-measures-20261106/`。动态列表一次快照不保证历史完整、撤销变更或其他监管机关覆盖，命中不直接形成治理结论，未命中不表示合格。
- 深交所上市公司监管措施与纪律处分页面分别为 `/disclosure/supervision/measure/measure/index.html` 和 `/disclosure/supervision/measure/pushish/index.html`，页面对应报表目录 `1800_jgxxgk_snapshot` 与 `1800_jgxxgk_cf_snapshot`；报表接口小样本HTTP 500，页面本身HTTP 200，故不能判定无记录或覆盖完成。证监会行政处罚与各派出机构监管措施分散公开，尚未验证统一完整查询；金融许可证系统存在验证码，未绕过。
- 依据既有隔离库只读重算并合并来源索引626家，财务来源在2026-11-03截点均current，发行人适用性均unverified。索引保留审计来源摘要、供应商原始类型代码、现金流参考、市场身份差异和上交所命中；发行人类型、行业地位、财务质量、治理及现金专项保持待核验。详见 `tmp/sse-all-measures-20261106/evidence-index-20261103.csv`，并非当日资格。
- 2026-11-06独立候选发现完成626家，红利578、成长548；与11月3日逐代码及所属池完全一致。四个指数成分日期11月5日，创业板10月30日；正式数据库摘要未变。盘中无法将当日未收盘交易日作为完整窗口，当前全流程收盘复核尚未完成。
- 用户要求全量检查：`verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv` 中 Ruff、Pyright和数据取数入口通过；pytest 5098通过、7跳过、1失败，完整日志 `tmp/full-check-20261106.log`，失败目录保留。唯一失败为静态页面测试硬编码旧JS缓存版本，页面和模块使用 `20261104-audit-clues-2`；同步测试断言后单用例1通过、测试文件Ruff通过。未重跑修正后的完整测试套件，不称全量重跑通过；审批拒绝了含递归清理的测试命令，移除清理后测试通过，临时目录保留。

#### 继续核查（2026-11-07）

- 内置浏览器打开深交所上市公司监管措施页面超时；监管措施和纪律处分两个公开报表接口各复测一次均HTTP 500、返回HTML错误页，不能把失败当作无记录。未绕过访问限制。同日随后改用 `/api/report/ShowReport/data` 端点复测成功，见下节。
- 11月6日隔离库此前仅包含候选发现任务，不含日历缓存；从正式日历数据库以SQLite备份复制到隔离目录后，只读计算11月7日窗口为2026-08-07至2026-11-06共60个交易日，缓存来源akshare:sina:XSHG，抓取2026-10-04、覆盖至2026-12-31。窗口可供后续核验，不表示成交额已采集。
- 只读核对任务：11月3日隔离库的财务、分红及成交任务状态均部分完成；11月6日隔离库仅有候选发现完成。当前截点完整财务、分红、基础资料及成交任务尚未获取，不能以历史来源冒充当前资格，亦未做全流程复核。未改正式业务库、行业映射或成员。

#### 交易所监管列表与正式依据核查（2026-11-07）

深交所监管措施、纪律处分接口复测：此前使用的 `/api/report/ShowReport` 返回 HTTP 500，本次改用 `/api/report/ShowReport/data` 后正常。监管措施目录为 `1800_jgxxgk_snapshot`、`TABKEY=tab1`，2026-11-07 声明 190 条、10 页、原始 190 条、解析 186 条、丢弃 4 条；纪律处分为 `1800_jgxxgk_cf_snapshot`、`TABKEY=tab2`，声明 136 条、7 页、原始 136 条、解析 136 条、丢弃 0 条。上交所统一监管列表 `BS_KCB_GGLL_NEW`、站点 28、渠道 10007—10010，2026-11-07 声明 5575 条、223 页、原始 5575 条、解析 5575 条、丢弃 0 条。全部来源另有 18 条记录因证券代码缺失或不属于沪深普通A股范围而不能绑定公司，索引保留计数但不归属任何公司。按来源文书 ID 与代码/日期/类型/标题组合校验无重复；采集失败会使旧快照不可用。

新增只读模块 `exchange_governance.py` 与命令 `radar exchange-governance-update`，逐页保存原文、请求参数、抓取时间和摘要，并分别记录声明总数、原始条数、解析条数、编码可用条数和丢弃条数；索引按证券代码绑定，命中保留原文页摘要、列表日期、原始措施类型及 `measure_category`（工作函/监管措施/处分/未知）。详情/审核包仅在 symbol、as_of、未审核及未启用正式筛选全部匹配时投影 `governance_clues`；不创建审核事实或改变共同条件。读取和复读使用文件锁，采集失败会使旧快照不可用。原始分页与索引位于隔离目录 `tmp/current-scope-20261107-7d3c1a9e5b6f4c2d8e0a1b3c5d7f9e11/.stock_robot/stock-exchange-governance/`。

隔离快照对 626 家匹配：211 家观察到 473 条记录（上交所 453、深交所监管措施 13、纪律处分 7）；类型以监管工作函 224、监管关注 80、通报批评 71、监管警示 55、公开谴责 25 为主，按 `measure_category` 分为工作函 319、监管措施 56、处分 98。上交所记录列表日期覆盖 2013-06-28 至 2026-11-06，深交所监管措施覆盖 2025-11-12 至 2026-11-06，纪律处分覆盖 2023-10-31 至 2026-11-06。列表日期不是文书落款或公开日期；动态列表不证明历史完整或撤销变更，未命中不表示治理合格，工作函不等同处分。该结果仅为研究线索，治理条件仍全部待核验。

发行人类型独立核查：用 2026-11-03 沪深上市名录中的 626 个公司全称，对「银行业金融机构法人名单（截至2024-12-31）」「保险机构法人名单（截至2023-12-31）」和证监会「证券公司名录（2026年9月）」做全称精确匹配。结果银行 30 家、保险 4 家、证券 2 家、其他金融（信托/金融租赁）2 家，其余 588 家未命中；未命中不能证明普通企业，且名录日期早于研究截点，只作待核验线索。明细在 `tmp/issuer-type-20261107/issuer-type-clues.csv`，银行/保险 PDF 原文与匹配摘要同目录。

行业地位：抽 600900、000333、600519、300750 读取主营业务范围、主营构成和经营讨论，可确认主营业务与市场边界线索，但不能得到独立、可追溯的市场份额或竞争地位数据；行业分类、指数成分和市值排名不构成龙头依据。行业地位继续全部待核验。财务质量与现金专项同样因发行人类型未正式核实、金融分支无已确认口径而保持待核验；普通企业 0.8 现金比率只作数值参考。

#### 当前截点与全流程复核（2026-11-07）

2026-11-07 重新执行候选发现：626 家，红利 578、成长 548，重叠 500；范围产物 SHA256 `616654618011860297fa11a65d1f2183bb1e25fbd9f12f41e0c15aebd81e756b`。指数快照日期为 000510、000922、H30269、000688 的 2026-11-06 和 399006 的 2026-10-30，均标记完整；与 11-03、11-06 逐代码及所属池一致。研究截点 2026-11-07，60 个已收盘交易日窗口为 2026-08-07 至 2026-11-06，日历缓存抓取 2026-10-04、覆盖至 2026-12-31。

隔离状态目录为 `tmp/current-scope-20261107-7d3c1a9e5b6f4c2d8e0a1b3c5d7f9e11/`，正式业务库未参与。任务及结果：财务红利池 `81d96aa30bf64714bc0bd2281c72b99a` 完成 578/578；成长池 `d5f65472cd9841ddbb156b1f41974fc4` 部分完成 547/548，688820 的数值条件待核验但来源为 current，不是来源失效。基础资料 `f053330e9dac41bf875ade23cc21cb1f` 完成 627/627 个通道，市场身份和市值来源均为 current。分红 `be142584dccc4a62bb198d6619d0544a` 为 472 完成/106 待核验，`2a3dee55f6e64148927cf7847c76bbd7` 为 424/124。成交额 `07324203315a4e878110a12fea2e2190` 为 574/4，`8a0ecfdd9b094a08bffdceacba0d8438` 为 544/4；4 家缺日与此前一致，未填零。成交额、分红和基础资料分别在隔离克隆库并行执行后合并任务记录；克隆库与主隔离库同源、共用原文产物，合并前校验终态、公司范围和产物摘要。

626 家逐公司复核结果：财务来源 current 626，成长研究参考 current 626；分红 current 500、partial 126，成交额 current 622、partial 4；市场身份 verified 616、pending 10（万科A、深桑达A、华侨城A、粤高速A、京东方A、新和成、兔宝宝、报喜鸟、方大炭素、中国人保，差异为空格、全角字符或 XD 前缀，按原样匹配规则保持待核验）；审计条件 615 通过、11 待核验。共同条件红利池 190 明确失败/388 待核验，成长池 185/363；去重后 220 家存在至少一项明确数值失败，406 家无明确数值失败但仍待核验，后者仅为研究关注线索。证券身份、治理、行业地位、财务质量、现金专项均为 626 家待核验；治理线索 211 家/473 条，其中近三年 71 家/98 条。正式筛选、成员发布、业务库写入和联网复核均为 0，`formal_db_unchanged=true`。

上述统计只说明当前来源的覆盖和数值条件，不代表正式入池；发行人类型线索未命中不证明普通企业，治理未命中不证明合格，工作函不等同处分，行业分类/指数成分/市值排名不构成龙头依据，分红 partial 不能当作零分红。明细为同目录 `company-conditions.csv`、`attention.csv`、`evaluation-details.csv` 和 `summary.json`。

验证：本次监管来源、审核包、风险线索和管理接口相关测试 88 项通过（`test_stock_exchange_governance.py` 21、`test_stock_manual_review_packet.py` 25、`test_stock_risk_warning.py` 16、`test_stock_radar_management.py` 26）；Ruff 通过，相关源码 Pyright 0 错误。626 家离线复核 0 个评估错误、0 次联网、0 次业务库写入；正式库 `radar.db` 的 SHA256 运行前后均为 `93FEF82662041BA0D6B348B59A0CC93795E3C1DFF7E7B36AA06E1123766917E3`。未重跑全量 pytest、真实服务或浏览器验收，最终门禁与验收仍由 Codex 完成。

#### 验收修复（2026-11-08）

监管索引复读改为从保存的 240 个原始分页按确定性规则重建索引，并与保存索引做规范化逐条比较（公司代码、内容、来源、记录标识、分页摘要、计数和来源元数据）。单公司读取使用重建结果，索引键错绑、内容修改、记录增删、重复或来源不符均降级不可用；任一分页缺失、损坏或来源不完整时，整个快照不可用，未命中公司也不能返回有效 `not_observed`。页面内容每次都重新读取并校验摘要，仅按原文摘要复用解析结果，不保留跨请求快照缓存；一次详情请求只做一次完整复读，另以登记身份复核 manifest/index 未变化。

后续验收复现了“只删登记页、同步重建索引”仍被判有效：原始响应声明 5575 条/223 页，登记和索引同步改为 5550 条/222 页后，中信证券等记录消失但快照仍 current。第一次 v3 修复只对照登记数字，仍可被同步改写绕过；本次改为每次复读直接解析原文声明：上交所 `pageHelp.total/pageCount/pageNo`，深交所 `metadata.recordcount/pagecount/pageno`。要求登记页数等于原文声明总页数、实际原文条数等于声明总条数、页码 1..N 连续唯一且与登记页码一致、每页声明总数和总页数前后一致，并继续核对 `received/parsed/dropped`；不再信任登记中的声明数字。回归测试改为“登记和索引已同步为 1 页/3 条、但原文第 1 页仍声明 2 页/2 条”，该场景在旧 v2 会通过、新实现必须不可用；另加原文页码与登记不一致的测试。

生产快照仍为 `exchange-governance-v3`，从保存原文重建后保留 `fetched_at=2026-11-07T22:24:56.655771+08:00` 和 `as_of=2026-11-07`，`rebuilt_at=2026-11-08T15:42:03.078628+08:00`、`rebuilt_from_version=exchange-governance-v2`；上交所 223/223 页、5575/5575 条，深交所监管措施 10/10 页、190/190 条、解析 186 条，纪律处分 7/7 页、136/136 条。按原始声明校验后快照仍为 current。

分类规则升级为 `exchange-governance-v2`：`监管工作函`为工作函，`监管关注`、`书面警示`、`监管警示`、`约见谈话`、`要求全体董事限期参加培训`为监管措施，`通报批评`、`公开谴责`、`公开认定`为处分，其余类型保留 `unknown`；不再按“含函/关注”宽泛推断。分类快照随后按 v3 完整性规则再次从原文重建（见上段），保留原 `fetched_at=2026-11-07T22:24:56.655771+08:00` 和 `as_of=2026-11-07`。626 家命中 473 条的分类由工作函 319、监管措施 56、处分 98、未知 0，变为工作函 224、监管措施 136、处分 98、未知 15；80 条“监管关注”从工作函改为监管措施，13 条“监管函”和 2 条“规范运作建议书”保留未知。旧 v1/v2 派生索引不再被当作新结果，必须通过 `radar exchange-governance-rebuild` 从原文重建。

`company-conditions.csv` 的 `financial_numeric` 改为读取 `financial_numeric_status`，现金比率仍单独读取 `cash_flow_reference`；新增 `financial_status_columns` 及盈利/现金状态不同的回归测试。新闻测试改为固定带时区时钟，明确过去与未来样本，生产过滤逻辑未改。

复算差异：`company-conditions.csv` 的 `financial_numeric` 有 219 行改变，分布由旧值 passed 431/ pending 98/ failed 97 变为 passed 486/ failed 139/ pending 1；000002 由 pending 改为 failed，688820 由 passed 改为 pending，600900 保持 passed。现金比率和现金状态字段未因此改变；`attention.csv` 仍为 406 家，`summary.json` 的条件计数、错误数和正式库不变标记一致。治理分类变化改变 12 家公司的 `governance_categories`，属“监管关注/监管函”重新归类，不改变命中公司数和记录数。

真实规模读取：正式快照 240 个分页全部复核约 0.85 秒，轻量登记身份约 0.03 秒，单公司 `load` 约 1.5—1.9 秒；50 次连续单公司读取约 93.6 秒，折算 626 家约 19—20 分钟，包含逐公司评估的完整复核约 25 分钟。每个请求都重新读取并校验全部分页摘要，仅按原文摘要复用解析结果；详情请求一次完整复读加一次轻量身份复核，不保留跨请求快照缓存。

定向验证：监管来源 32 项、财务状态列 1 项、新闻固定时钟 2 项，共 35 项通过。全量门禁 `verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv` 通过：Ruff、Pyright、数据取数入口通过，pytest 5139 通过、7 跳过、0 失败，耗时 6 分 0 秒，日志 `tmp/acceptance-fix-testcoverage-full-20261108.log`。修复后重跑的 `company-conditions.csv` 626 家、0 错误；`financial_numeric` 219 行改变（旧 passed 431/pending 98/failed 97，新 passed 486/failed 139/pending 1），治理分类改变 12 家展示类别，`attention.csv` 仍 406 家，`summary.json` 条件计数不变，正式库不变标记为 true。

回归测试样本已进一步修正：漏页用例同步 `index.sources` 和 `date_range`，错页码用例同步来源引用、索引来源及该页记录的 `page_sha256`，使登记、索引和记录内部一致，只由原文声明触发失败。已在临时 `e94abec` 工作树验证：两个测试在旧实现上均返回 `current` 并失败，在当前实现上均通过。

### 正式判断依据接入（2026-11-08）

用户确认四项口径后接入：发行人类型、治理、财务质量按以下规则，行业地位保持待核验。程序和人工审核仍分离，未创建 `review=confirmed`、未发布成员。

1. 发行人类型：新增 `data/financial_issuer_rosters.json`，保存 2025-06-30 银行业/保险业/金融控股公司法人名单和 2026-09 证券公司名录的正例全称、来源 URL 与日期；`issuer_type.py` 做全称精确匹配，未命中且证监会行业非金融、名称不含金融牌照业务才判普通，金融业/金融业务名称保留未分类。`company_profile_source` 输出 `issuer_type`/状态/来源，`company_profile_admission` 在官方名录、市场和类型同时有效时通过证券条件。2025-06 名录早于 2026-11-07，不覆盖此后新设、合并、更名。
2. 治理：`formal_rules.merge_governance` 仅对近三年公司对象纪律处分硬失败；公司对象监管措施待核验；工作函/未知不失败；标题明确财务造假、虚假记载、资金占用、违规担保的文书硬失败，至少 5 个完整年度后人工复核。未命中仍待核验。
3. 财务质量：`financial_source` 从已保存财务原文提取 2025 年报类型指标；银行资本充足率≥10.5%、拨备覆盖率≥150%、不良率≤5%，证券风险覆盖率≥100%、流动性覆盖率≥100%，保险综合偿付能力≥100%；普通企业沿用三年累计经营现金流/归母利润≥0.8、盈利和审计条件。普通企业现金流比率现可在同截点结构化财务来源下独立通过，不再要求人工现金事实；现金专项仍待来源/用途核验，金融企业不套用 0.8。
4. 规则版本：共同规则仍为 `common-approved-2026-10-29-v3`（避免财务任务失效），新增 `formal-2026-11-08-v2` 并写入报告和归档校验；风格版本升级为 `styles-research-2026-11-08-v10`。旧归档须重评。
5. 新增测试：发行人分类边界、治理对象/期间/严重事项、财务质量各分支、普通企业结构化现金比率和文档/审核分离；相关准入、档案、API 测试已通过。
6. 2026-11-07 隔离快照按新口径重跑 626 家、0 错误、零联网，正式库不变。发行人类型：银行 30、保险 4、证券 2、其他金融 2、普通 582、未分类 6（000415、000617、300033、300059、300803、600864）。证券条件通过 608、待核验 18（10 家名称原样匹配差异、6 家未分类、2 家其他金融；中国人保同时受名称差异影响）。治理条件失败 10、待核验 616；近三年公司对象纪律处分 9 家、公司对象监管措施 11 家，另有资金占用工作函 1 家触发严重事项；211 家有观察记录，415 家未命中仍待核验。财务质量通过 469、失败 134、待核验 23：银行 28/30 通过、2 失败（华夏银行、民生银行拨备覆盖率不足，资本充足率全部达标），证券 2/2 通过，保险 3/4 通过（中国人保证券身份未过），其他金融 2 家待核验，普通企业 436 通过、132 失败、14 待核验。成长池共同失败由 254 降至 190：63 家仅因现金比率被间接硬失败的成长候选恢复，另 1 家来自银行总资本充足率字段修正。红利池普通企业现金比率 369 通过、78 失败、96 待核验、35 不适用；现金专项 626 家仍全部待核验；行业地位 626 家仍全部待核验。关注线索由 406 降至 339，仍无法形成正式通过闭环，成员发布 0。
7. 明细在 `tmp/current-scope-20261107-7d3c1a9e5b6f4c2d8e0a1b3c5d7f9e11/` 的 `company-conditions.csv`、`attention.csv`、`evaluation-details.csv`、`summary.json`；全流程日志 `tmp/current-scope-20261107-formal3-20261108.log`。该结果只代表 2026-11-07 截点，不冒充后续日期资格。
8. 最终稳定状态全量门禁 `verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv` 通过：Ruff、Pyright、数据取数入口通过；pytest 5152 通过、7 跳过、0 失败，耗时 7 分 52 秒，日志 `tmp/fix958-full3-20261108.log`。全流程 626 家、0 错误、零联网、正式库不变；`attention.csv` 339 家，common 通过 0 家，成员发布 0。治理失败 10 家、待核验 616 家；财务质量通过 469、失败 134、待核验 23；现金专项和行业地位仍 626 家全部待核验。真实服务和内置浏览器未因本轮无页面改动而重复运行，交回 Codex 独立验收。

### 验收缺陷修正（2026-11-08）

1. 成长池不再被现金比率间接硬淘汰：共同财务质量只使用盈利和审计条件；普通企业 0.8 只在红利价值池作为硬条件、成长趋势池作为质量提示。成长池共同失败由 254 降至 190，其中 63 家仅因现金比率被误硬失败，另 1 家来自银行资本字段修正。
2. 银行资本充足率改用总资本充足率字段 `NEWCAPITALADER`，`FIRST_ADEQUACY_RATIO` 仅保留为一级资本充足率参考。浙商银行由错误值 9.60% 修正为 12.12%；银行财务质量 28/30 通过，仅华夏银行、民生银行拨备覆盖率不足。
3. 归档绑定治理来源和正式规则版本：`governance_source` 写入评估输入并在读取时复算比较，监管原文损坏/来源不可用时旧档案失效；`_freshness` 对 `formal-2026-11-08-v2` 变化增加失效原因。
4. 发行人分类检查完整名称和行业必需字段；行业为空、名录缺失/损坏或结构不完整时不再判普通，返回待核验/不可用；移除永久缓存，每次重新读取并按文件 SHA 绑定，名录变化会使上市资料来源失效。
5. 台账重建为与最终 `company-conditions.csv` 同源，发行人、证券、治理、财务质量、现金、审计等状态逐列核对 0 差异；最终产物统一在 `tmp/formal-final-20261108/`。
6. 新增回归：成长池不因现金比率失败、银行总资本字段、发行人缺失行业/损坏名录、归档正式版本失效、治理来源变化失效。全量门禁 5152 通过、7 跳过、0 失败。

### 完整收尾执行（2026-11-08）

1. 发行人称谓：对官方名录简称仅做可复核规范化（NFKC、去空白、去 XD/XR/DR 前缀、同代码唯一简称前缀保护），解决万科Ａ、深桑达Ａ、新 和 成、XD方大炭、XD中国人等 10 家格式差异；空名称不得匹配；6 家未分类和 2 家其他金融保持不变。证券条件 618 通过、8 待核验。
2. 行业地位：按用户确认的严格第三方/协会引用口径，仅接入 9 家带原始 F10 响应 SHA、原文引用、同报告日期、公告披露日不晚于评估日且排名 1-3 的第三方/协会份额或排名披露（000333、000538、000651、300373、300438、301165、301377、600887、605499），其余 617 家待核验。来源为东方财富 F10 披露原文及报告日期，状态文件在隔离状态 `stock-leadership/leadership.json`；未使用候选池营收排名。
3. 现金专项：普通企业采用分红优先口径——三年经营现金流>0、覆盖现金分红、利息保障≥1、现金余额≥0；资本开支超过经营现金流只作提示，不直接失败；分红字段缺失保留待核验，不能按零处理。银行/保险缺少专用流动性和分配约束字段，继续待核验；证券用风险覆盖率和流动性覆盖率。红利池 416 通过/105 失败/57 待核验，成长池 405 通过/105 关注/38 待核验。
4. 治理：用户选择新增内控/交易所评价来源，但现有公开接口未取得可批量复核的结论；不把标准财务审计或未命中负面列表冒充治理通过。治理保持 10 失败/616 待核验，作为明确未完成事项。
5. 财务和数据例外：财务质量 474 通过/139 失败/13 待核验（8 家发行人未分类/其他金融、4 家审计待核验、1 家缺财务数值）；审计 615/11；分红 partial 126、成交额 partial 4 保留原因。
6. 626 家全流程 0 错误、零联网、正式库不变；关注线索 327，common 通过 0，成员发布 0。产物在 `tmp/formal-final-20261108/`，日志 `tmp/current-scope-20261107-complete3-20261108.log`。

7. 最终稳定状态全量门禁 `verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv` 通过：pytest 5171 通过、7 跳过、0 失败，耗时 7 分 20 秒，日志 `tmp/complete-closure-full3-20261109.log`；Ruff、Pyright 通过。逐公司例外清单在 `tmp/formal-final-20261108/exceptions.csv`，与 `company-conditions.csv` 同源。治理正向来源仍未取得可批量复核结论，项目不宣称完整闭环。

### 证据链复核与治理评价接入（2026-11-09）

1. 证据链复核：行业地位、现金专项、治理、发行人身份、财务质量/审计和归档/草案统一审查。行业地位把公司、引文、报告期、口径、排名、第三方来源和公告绑定到同一原文与同一事项；拒绝否定排名、其他公司排名、多排名、跨记录日期、跨公司公告和模糊份额。公告新增原始响应 SHA、`art_code`、证券代码、标题期间和披露日校验；份额限定词必须写入数值本身。现金专项绑定公司、币种、披露/更新日，明确 `ASSIGN_DIVIDEND_PORFIT` 为“分配股利、利润或偿付利息支付的现金”。财务类型分列总资本充足率与一级资本充足率，并交叉校验财务公司类型与发行人类型。发行人官方名录来源不得晚于截点，名称改为精确匹配。归档刷新失败不得回退旧 `cached`。正式规则版本升至 `formal-2026-11-09-v3`，提交 `4a277c2`。
2. 治理正向依据：用户确认“交易所年度信息披露评价 A/B 级且无公司对象硬失败”作为治理通过。新增 `data/exchange_disclosure_evaluation.json`，固化沪深交易所 2024-2025 年度评价结果（评价期 2024-07-01 至 2025-06-30，上交所 2025-10-24、深交所 2025-10-31 发布），保存来源 URL、PDF SHA-256、字节数和 5104 条等级。`disclosure_evaluation.py` 只读取已发布且覆盖截点的评价；`merge_governance` 在无纪律处分、无严重事项、无公司对象监管措施时允许 A/B 通过，C/D、未覆盖或未发布待核验，硬失败优先。正式规则版本升至 `formal-2026-11-09-v4`，旧归档失效。
3. 治理接入后：通过 578、待核验 38（C/D 24、未覆盖 5、A/B 但存在公司对象监管措施 9）、失败 10。9 家行业地位公司中 000333、000538、000651、300373、301165、301377、600887 共 7 家达到共同条件通过；300438 因 2024 年利润为负财务质量失败，605499 为 C 待核验。其余 617 家仍因行业地位待核验。
4. 9 家行业地位重新核验：9 家全部仍通过；000333 的声明值由“22%”更正为原文“超过22%”，000538、301165 的 `metric` 更正为 `rank`；公告原始响应和披露日全部补齐。原三个复现缺陷及否定排名、跨记录、跨公司公告均有反例测试。发行人称谓精确匹配使 600516、601319 转待核验，证券条件 616/10；下游财务质量 473/138/15、现金专项 449/104/12/61、审计 615/11。
5. 行业地位市值路径：用户确认方案 M1——626 家候选池内申万二级当前公司总市值前 3（同业≥5 家），并保留现有 9 家第三方/协会显式依据。新增 `industry_scale.py` 与 `stock-leadership/scale.json`：市值来源绑定每家公司 `manifest_sha256`、行业映射 SHA、候选池 scope SHA；同截点、同池排名，缺失市值不按 0 参与；只称“候选池内规模地位”，不称全市场行业龙头或市场份额。正式规则版本升至 `formal-2026-11-09-v5`。
6. 市值路径接入后：行业地位通过 145（138 家候选池前 3 + 9 家显式依据 - 2 家重叠）、待核验 481；共同条件通过 114（红利池 109、成长池 106），硬失败 299，关注线索 327。300438 财务质量失败、605499 治理 C 等原有例外保持不变。其余 481 家仍因行业地位待核验。
7. 复算：626 家、0 错误、零联网、正式库不变。成交额解码子进程在本次复算中反复挂起，复算跳过该非治理字段，并用同快照 v3 的已核验成交额/流动性结果回填；其余字段均按新代码重算。产物在 `tmp/formal-final-20261109/`，含 `industry-scale.json`、`governance-positive-proposal.md`、`leadership-market-cap-proposal.md`。
8. 全量门禁和最终提交结果见交接报告。

### 审计字段语义与 partial 收尾（2026-11-09）

1. 审计：`OSOPINION_TYPE` 与主意见同为“标准无保留意见”时不再视为附加意见，审计分类版本升至 `structured-audit-2026-11-09-v2`。600036、600585、601319、601398、601658、601988 六家由 pending 转标准意见；000002、000547、002555、002602、601155 五家非标准主意见仍 pending。审计条件由 615/11 变为 621/5。
2. 审计修复后共同条件通过由 114 增至 116：新增 600036、601398；601658、601988、600585 仍因行业地位待核验，601319 仍因证券身份待核验。
3. 分红 partial：126 家已保存来源逐家复读，全部为 pending；112 家 eastmoney/cninfo 均为“分红记录缺失或冲突”，14 家 eastmoney 解码 ValueError 且 cninfo 仍 pending。没有可离线恢复的明确 pass/fail，保持待核验，不按零、不回退。
4. 成交额 partial：4 家（000831、002129、002155、601238）产物只有 `as_of`/`symbol`，没有可校验 payload；保持 partial，不影响共同条件。
5. 全量门禁和最终提交结果见交接报告。
6. 证券简称窄归一：仅对交易所 XD/XR/DR 除权标记下被截断的简称做窄归一（如 XD方大炭→方大炭素、XD中国人→中国人保）；无标记截断仍不猜。600516、601319 恢复证券身份，证券条件回到 618/8。6 家未分类金融（000415、000617、300033、300059、300803、600864）和 2 家其他金融（600816、600901）继续待核验，未擅自人工归类。
7. 现金专项 60 家 pending 分类：30 银行 + 4 保险不适用；6 未分类 + 2 其他金融；13 普通缺三年数据或新上市；5 普通缺利息保障。12 家失败为真实不满足；没有代码误判。
8. 最终复算 626 家、0 错误、零联网、正式库不变：证券 618/8、治理 578/38/10、行业地位 145/481、财务质量 475/139/12、审计 621/5、现金专项 449/105/12/60；共同条件通过 116（红利池 111、成长池 108），硬失败 299，关注线索 327，正式成员 0。

### 最终冻结（2026-11-09）

1. 用户接受现有待核验清单，冻结 116 家共同条件通过名单：两池都有 103、仅红利池 8、仅成长池 5；普通企业 107、银行 9；治理 A 96、B 20；行业地位为候选池市值前 3 的 109 家、第三方显式依据 5 家、两者都有 2 家。
2. 正式成员仍为 0：两池风格条件（现金专项、分红历史等）还有 pending/failed，正式发布保持关闭；116 家不是正式成员。
3. 接受不再强查的清单：行业地位 481、治理失败 10/待核验 38、证券身份 8、审计 5、现金专项待核验 60/失败 12/关注 105、分红 partial 126、成交额 partial 4。
4. 产物新增 `common-pass.csv`（116 家逐公司）、`manual-review.csv`（8 家证券、5 家审计、60 家现金、12 家现金失败、105 家现金关注、126 家分红 partial、4 家成交额 partial）、`final-acceptance.md` 和 `final-acceptance.json`；manifest 绑定 commit `28d6f6e`。

### 结构化收口与规则调整（2026-11-09 v6）

1. 方式 A：`evaluate_store` 增加 `structured_only=True`，正式路径不再读人工证据；人工复核模块只作审计留痕。`security` 不再作为共同条件，从正式结果移除；发行人类型保留为财务/现金路由。`company-conditions.csv` 的 `security_condition` 记为 `not_applicable`。
2. 行业地位改为候选池内申万二级当前总市值前 5（同业≥5 家），保留 9 家显式依据。行业地位通过 237（前5 230 + 9 家显式 - 2 家重叠），待核验 389。
3. 治理 C/D 直接失败。治理 578 通过、14 待核验（未覆盖 5 + 公司对象监管措施 9）、34 失败。
4. 审计分层：保留/否定/无法表示意见失败，带强调事项段/其他事项段待核验，重复标准意见忽略。审计 621 通过、4 待核验、1 失败（002602）。
5. 现金专项：银行/保险/其他金融 `not_applicable`，普通企业仍按三年现金流/分红利息/利息保障/现金余额。现金 449 通过、105 关注、12 失败、24 待核验、36 不适用。
6. 最终复算 626 家、0 错误：共同条件通过 178（红利池 173、成长池 164；两池 159、仅红利 14、仅成长 5），普通 163、银行 15，治理 A 139、B 39；财务质量 475/140/11；硬失败 308，关注线索 318，正式成员 0。
7. 产物更新：`common-pass.csv`（178 家）、`manual-review.csv`（审计 4、现金 24/12/105、分红 126、成交额 4）、`final-acceptance.md`；规则版本 `formal-2026-11-09-v6`。
8. 选项B复核：178 家中风格待核验唯一 13 家（8 家 dividend_history、4 家 cash_review、1 家 liquidity）。8 家分红来源不能证明三年完整覆盖；4 家现金缺三年字段/利息保障；1 家成交额无 payload；均不能从 2026-11-07 已保存来源离线补齐。红利候选维持 129、成长 159、并集 172；33 家红利硬失败维持失败。
9. 最终冻结 172 家风格通过候选：129 红利 + 159 成长，116 两池、13 仅红利、43 仅成长；178 家中 6 家未进任一候选。正式发布仍为 0；不再补 13 家、不放宽 33 家。产物新增 `formal-candidates.csv/json`、`final-freeze-172.md`。
10. CNINFO 同年多笔分红修复：原解析把“同一年度同一天多笔”或“同日年报+季报”误判为全局冲突；改为每个目标年度至少一条正向实施记录即可通过。300750、300760、600803、603259 四家转通过，红利候选 129→133、两池 120、仅成长 39、并集仍 172；另有 1 家不在原 13 家清单内的分红待核验也转通过（因其他风格阻断未进候选）。剩余 9 家风格待核验（4 家 dividend_history、4 家 cash_review、1 家 liquidity）；33 家红利硬失败不变。门禁 5210 passed / 7 skipped / 0 failed。
11. 现金 END_CCE 字段补读：`END_CASH`/`END_CASH_EQUIVALENTS` 缺失时回退读取 `END_CCE`，不改变规则。002352 顺丰控股转通过；601800 中国交建现金专项 failed，但成长池只记 attention，成为成长候选。红利候选 133→134、成长 159→161、两池 120→121、仅成长 39→40、并集 172→174；剩余待核验 9→7（4 分红、2 现金、1 流动性）；33 家红利硬失败不变。门禁 5215 passed / 7 skipped / 0 failed。
12. 利息保障补算：利息保障倍数缺失时，优先用利润表净利润与财务费用计算；净财务费用≤0 按无净利息负担处理。600141 兴发集团补算 5.92，现金专项通过；红利 135、成长 162、两池 122、并集 175；剩余待核验 7→6（4 分红、603087 现金、002155 流动性）；33 家红利硬失败不变。门禁 5216 passed / 7 skipped / 0 failed。
13. 年报利润分配独立判定：新增 `dividend-plan-2026-11-10-v1`，从年报利润分配章节识别“不派发现金红利/不分配不转增”与“每10股派现”，输出 no_dividend/positive/pending；不按公司代码或用户答案硬编码。补全计划的 `dividend_history` 来源改为年报利润分配章节，不再只依赖分红实施接口；未归档年报原文时保持 pending。补 4 个文本规则测试；门禁 5220 passed / 7 skipped / 0 failed。
14. 双源缺失独立判定：聚合 626 家、10194 行分红送配原始数据，东财某年度完全无行但巨潮有正分红的反例为 0；新增 `evaluate_dividend_absence`，东财与巨潮都完整且某年度双方均无正分红记录时判 no_dividend。002384、688347、000792 独立 failed；688981 因东财来源 failed 仍 pending；603087 API 证明三年分红，2023 现金金额仍 pending；002155 流动性 pending。红利候选维持 135、成长 162、并集 175；红利风格失败 33→36、待核验 6→3。门禁 5223 passed / 7 skipped / 0 failed。
15. 三个补全适配器：新增 `completion_adapters.py`，统一 `AdapterResult`，实现年报现金流解析（四个字段）、60 日成交额校验、来源重试；计划三项分别映射 603087→annual_report_cashflow、002155→market_turnover_60d、688981→dividend_source_retry。适配器只解析/校验证据，不猜值、不改股票池；抓取器由调用方注入。补 6 个测试；门禁 5229 passed / 7 skipped / 0 failed。
16. `stock-completion-apply`：读取计划并校验 `plan_sha256`（哈希排除 `members_published`），`--dry-run` 只回放、`--yes` 才执行；无 reviewer/reason。本地证据入口 `--annual-dir/--turnover-dir/--retry-dir` 驱动三个适配器，输出 completion-run；证据缺失保持 pending，不改股票池。补 4 个执行测试。
17. 东财 F10 页面负证据：626 家中东财明细 612 家完整，14 家 688 开头失败；F10 页面完整且明确列示 2023-2025“不分配不转增”。新增 `evaluate_eastmoney_page_absence`，明细失败时回退页面；688981 独立判 failed，不再 pending。红利候选维持 135、成长 162、并集 175；红利风格失败 36→37、待核验 3→2（603087/002155）。补 3 个页面测试；ruff/pyright 全量通过，定向 124 passed。
18. 补全落地：002155 补 2026-08-07 至 2026-11-06 共 60 个交易日成交额（55 个有成交 + 5 个停牌日 0 成交，合计 760.76 亿元），`liquidity` passed；603087 补 2023-2025 年报现金流量表（2023 分配股利及利息 168,462,000.00，2024 420,547,253.00，2025 597,525,269.00；OCF 合计 14.31 亿元覆盖 11.87 亿元，利息保障 3393），`cash_review` passed。红利候选 136、成长 164、两池 123、仅成长 41、并集 177；待核验 0、红利风格失败 37、正式发布 0。
19. 统一补全接口清理：计划只 load 一次分红来源；适配器只消费唯一 provider（年报走 `annual_batch` 发现/归档/`extract_cashflow_text`，成交额走 `turnover_source`）；apply 只保留 `ADAPTERS` 注册表，删除本地证据目录、`providers` 旁路、if/elif 和 `SourceRetryAdapter`；新增 `stock-completion-recheck` 自动回灌评估明细。删除 `parse_turnover_rows`、手动 PDF 和东财日线分支。定向 21 passed，ruff/pyright 通过。注意：清理后的接口无法复现旧的东财日线 002155 证据；重跑会回到 176/1，177 是清理前手工 overlay 的结果。
20. 东财日线正式来源：新增 `eastmoney_history`，带 URL、`bounded_provider_call`、原始响应缓存/SHA、60 日窗口、停牌日 0 成交；`turnover_source` 通过 provider 分支校验，不信任保存的 daily；completion provider 优先现有任务来源，缺失时用东财正式来源回填。002155 实测 60 日、5 个停牌日 0、合计 760.76 亿元；603087 年报归档抽取三年现金流。补 3 个东财来源测试；定向 24 passed，ruff/pyright 通过；177 现在可由统一接口复现。

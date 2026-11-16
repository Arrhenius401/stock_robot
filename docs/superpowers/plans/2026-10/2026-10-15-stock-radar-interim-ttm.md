# 第一阶段：季度/半年报、TTM与年度事实接通计划

日期2026-10-15，基于664ace0。用户明确要求持续执行、无需确认；沿用已确认四阶段与design-before-implementation技能，不为子项重复确认。此阶段后仍继续成员管理等，不把技术子项当新交付阶段。未确认的股息/估值/成长/趋势数字仍待配置，正式筛选false。

## 已探针事实和样本

已使用现有collect_documents取得官方PDF（原文/tmp不提交）：
- 巨潮2026-04-30/1225262110.PDF，600900 2026Q1，SHA3536efda5117d4ef071729a16b9de8449846aedca0a8f2d7ff9a5eafa2cafe84，当前/上年同期/增幅3列，金额表第1页。
- 巨潮2026-08-31/1225534923.PDF，2026H1，SHA738de3b36a31646f344cefaf90b4f149d653c3f68e89f4f8d14bdf02fc9c65a2，主要会计数据第6页，当前/上年同期调整后/调整前/增幅4列。比较期间同一控制合并重述，不能自动认定可比。
- 巨潮2025-10-31/1224776945.PDF，2025Q3，SHA1efb73a0c2327419f6c9d571bd34814f8c4672cbc4d3def87c414e65b3f6d3c0，表第2页，同时单季度/年初累计，调整列共8列，OCF前四列不适用。

网站另有同报告不同上传版，不把文件上传日当交易所原披露日；正式样本用上述巨潮路径日期。测试静态资源只裁身份标题及表页主要会计数据：tests/resources/radar/cninfo-600900-2026-q1-accounting.txt、...2026-h1...、...2025-q3...。保留原始分页和pypdf文本断行，完整PDF留tmp/stock-interim-probe/pdf。

## 1. 季度公告及保守解析

公告发现增加mode=interim_report，三个关键词第一季度报告/半年度报告/第三季度报告，复用精确公司身份/分页/源429停止/15秒/缓存。只完整报告，摘要英文无效；更正修订等保留pending_review不自动解析，带fiscal_year/period_months/period_end、kind=interim_report，尚不确认latest。查询完成只代表本次关键词范围，不代表财报最新已核实。

新增interim_extract.extract_interim_text(pages,*,symbol,source_url,disclosed_on,as_of)，返回financial_extract兼容base字段financial_proposals及period信息。识别Q1/H1/Q3，使用year_to_date累计基础，period_months=3/6/9、period_end对应月末。current和明确比较列分别产year/previousyear，比较项保留comparison_basis=current/previous_unadjusted/previous_adjusted_after；重述comparison_requires_review。只支持明确人民币单位/列布局/严格数字token；Q3只选年初累计，不用当季当累计。Q1/H1支持3列普通，调整布局须明确调整后/前顺序和4列；Q3支持4列普通（单季金额/增幅/累计/增幅，仅当前累计）及已探针8列调整布局（当前累计索引4、上年同期调整后索引6）；不适用占位仅允许非目标季度列。模糊/多表/冲突/未知单位/未来/身份错误保持pending，金额不猜。页码/摘录/原始列及PDF摘要留档。

financial_extract.extract_archived_financials增加可选text_extractor回调，默认仍年报函数；复用相同25MiB/1000页/首20页/路径/hash/签名边界。新extract_archived_interims包装该执行器。

## 2. TTM纯计算与来源

新增interim_ttm.prepare_ttm(symbol,evidence,as_of)->dict，独立纯函数，不导入financial_facts以避免循环。数据来自financial_proposal年度和quarterly_financial_proposal累计建议、quarterly_financial_document(含失败文档期末)、admission_reporting_coverage。

季度fields延续年报metric/amount_yuan/currency/scope等，新增fiscal_year、period_months(3/6/9)、period_end、period_basis='year_to_date'、comparison_basis；披露须晚于period_end且<=as_of，来源/身份/元单位及审核状态校验同年报。无审核raw可计算研究建议，明确驳回/过期/未来审核不可用。等值合并、冲突None，Decimal精确加减用_sum_amounts，不把负/零丢掉。

TTM=FY(y-1)+YTD(y,m)-YTD(y-1,m)，仅3/6/9月累计，不以年报直接回填TTM。归母/扣非分别计算，收入/OCF可作为研究metric但不自动启用成长阈值；每项给三个分量/来源/计算式/amount/status。选择最新已知报告period_end（所有合法身份的季度proposal、document及coverage中的max），新期间缺数据/失败不得回退旧完整期间来声称最新。没有季度period返回None，年度只保留annual。

最新和可比范围需要显式已审核admission_reporting_coverage：payload={period_end,coverage_through:as_of,report_types_complete:true,scope_comparable:true,basis:非空}，有效来源/90天审核、无同组冲突，且period_end等于最新已知期末。只有这份证据及分量均齐全才latest_announced=true；否则数学结果可展示但latest_announced=false，不能放行TTM。coverage事实不能由查询完成自动确认。冲突coverage也不能选有利版本。

返回稳定version/symbol/as_of/period_end/period_months/disclosed_on/metrics（key parent_profit_yuan/deducted_parent_profit_yuan/revenue_yuan/operating_cash_flow_yuan，每项status/amount_yuan/sources/components）/latest_announced/coverage_evidence_ids/dependency_inventory/unresolved/requires_review/formalfalse。来源refs与financial_facts.field_sources相同追踪基础，包含evidence_id/review_id/source_url/content_sha256/disclosed_on和rawfields/origin/原值，不伪造PDF。

## 3. 数据库与年度准备器接通

research_store.import_review_packet支持quarterly_proposals/documents到quarterly_financial_proposal/document，不改变原financial_proposals；文档记录period_end允许失败报告阻止旧期间回退。identity payload对quarterly_proposal同样fields/origin。admission_reporting_coverage作为显式独立事实通过既有add/review保存。

financial_facts.prepare_financial_facts为profit组附prepare_ttm结果：ttm={period_end,disclosed_on,parent_profit_yuan,deducted_parent_profit_yuan,latest_announced}，额外ttm_sources/ttm_summary保存完整算式，不把raw建议变审核；无季度数据仍None。cash组仍年度且scopefalse。

只profit有季度相关资料时_dependencies.version=2，inventory包括本公司全部financial_proposal/quarterly_financial_proposal/quarterly_financial_document/admission_reporting_coverage的ID和审核版本；cash和纯annual保持v1。验证器读取所有这些原文及security，对version1/2相应名录检查，再按同准备器完整重建。v1旧利润遇新季度资料重建不一致即失效；不能用旧annual覆盖新亏损或绕过latest。无deps既有手工事实保持原审核边界。

## 4. CLI与端到端

新增interim-discover/interim-extract复用既有发现与归档输入保护；interim-review-packet将提取结果包装为可导入quarterly_proposals/documents审核包，带内容hash和未核实标志（不自动导入/确认）。原stock-financial-facts-prepare命令可同时准备年度和季度TTM，不增加自动启用选项。可沿用dividend-materials/archive命令下载发现的documents；资源预算和source429不变。

测试先行。解析代理只interim_extract.py/tests+三份资源；TTM代理只interim_ttm.py/test_stock_interim_ttm.py；父发现/共享执行器/存储/组装/CLI/集成/映射/文档。S级独立双审查及复审；真实三份PDF提取/packet/合成库年度+季度TTM保存、coverage未审不能最新/上游新报告失效。实际库不替用户审核或入池。verify按相关范围，不全量、不重复UI验收。完整事项分为可独立审查两笔本地提交：季度发现提取审核包；TTM计算及存储来源接通。过程中持续推进不等逐项确认；不推送、不发布成员。

## 第一笔实际验证（2026-10-15）

- 官方三份完整PDF经共享归档wrapper重验均matched_template，每份8条建议，共24条；产物tmp/stock-interim-probe/extracted-explanation-recheck.json。
- 真实Q1/Q3变动说明与页眉曾污染表识别，已修准确表头及非目标行边界，并保留真多表拒绝。单位/币种全部声明须一致，不能只挑首个。特殊标题词与发现器对齐。
- 发现/PDF双向期间错链均清空建议，记录两个期末并取合法较新期末作失败文档阻断线索；非金额依据。
- 审核包导入本地验收库新增29条，stocks_companies=685、stocks_evidence=801、stocks_reviews=0、stocks_source_batches=4；members_published=0。统计第一次误用无前缀表名失败，已通过sqlite_master确认后重查。
- 修改测试映射以覆盖解析器、命令、共享wrapper及季度资源。相关检查结果随提交记录补充；未运行全量回归，未重复无改动的UI验收。

第一笔门禁：verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv：Ruff通过，Pyright 0错误，517项相关测试通过（64.60秒），测试目录已清理。独立规格/质量审查发现项已收敛，第一笔复审通过。未运行全量回归。

## 第二笔真实资料只读探针

第一笔导入后，stock-financial-facts-prepare对600900读取年度和三份季度建议，得到2026-06-30 TTM：归母36202151477.37元，扣非34549413234.25元。分别为2025全年+2026H1-2025H1；完整来源保存在tmp/stock-research-acceptance/financial-facts-interim-cypc.json。原文存在比较重述，尚无独立覆盖/可比审核，因此latest_announced=false、未保存或审核。利润依赖v2，现金依赖v1。旧不可变评估保留，freshness.current=false，原因source_revision_changed:600900；没有覆盖旧评估。

纯TTM规格审查发现真实失败文档无PDF摘要时会丢新期阻断线索，正在补真实packet→SQLite回归；期间线索与金额来源校验必须分开。季度comparison_basis限定当前/上年同期未调整/上年同期调整后，拒绝单季度等自相矛盾元数据。

第二笔完成：纯模块及SQLite集成158项测试通过；最终verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv：Ruff通过、Pyright 0错误、335项相关测试通过（53.67秒），临时目录已清理。独立规格和质量最终PASS；质量探针2436次坏类型/数值变异无未捕获异常。真实只读探针最终重跑结果相同，利润v2绑定51条上游，financial_verified=false、saved=null。未运行全量回归，正式成员0。

## 2026-10-18全范围推进与短标题修复

研究截点仍固定2026-10-17；本次抓取实际发生于2026-10-18，不声称已取得历史时点快照。既有626家公司范围不变，先处理资料覆盖，再判断选股资格。检测到此前年度采集及后续离线整理进程均已退出、状态文件仍停在等待，故从检查点恢复；年度采集已处理626/626，复用620家，新增6家，失败及导入失败均0。随后续跑既有离线重解析命令，保留原文与全部待审边界。

先用TCL科技、美的集团、宁德时代、招商银行、贵州茅台、长江电力、金山办公、中芯国际8家公司运行通用interim-discover，原查询发现66份报告。发现部分深市公司只有半年报，不能将query_scope_complete解释为报告齐全。对同一官方接口补查“一季度报告”“三季度报告”，真实返回中文“2026年一季度报告”“2025年三季度报告”；此前带“第”的查询只返回英文报告，且标题解析不接受短写法。

小范围修复：季度关键词改为“一季度报告/半年度报告/三季度报告”，真实接口同时返回带“第”的英文标题及不带“第”的中文标题；发现与PDF标题识别均允许可选“第”，报告期归一，保留英文/摘要排除、修订待审、公司身份及披露日验证。修复不增加查询次数，也不放宽财务条件。独立研究日期及最新报告覆盖仍必须核验，不能仅凭关键词查询完成声称最新。

离线资源`tests/resources/radar/cninfo-000100-interim-titles.json`来自2026-10-18上述两次查询，各保留前两条原始公告对象（每次一条英文、一条中文），其余记录裁去；保留HTML高亮、标识、公司代码、日期与路径字段，以真实格式复现遗漏。完整响应留在`tmp/quarter-discovery-20261017/short-title-probe-*.json`，资源映射至公告与季度相关测试。另用已有真实会计数据文本只替换标题的变异测试，验证期间与金额均不改变。

按B级小修由主会话核对diff，不派代理。测试先行出现真实短标题拒绝及新测试调用记录形状误用，修正后公告发现、季度提取/审核包、共享分红及CLI相关146项通过；资源映射单独1项通过，全部改动Python文件Ruff/Pyright通过。未运行全量，无UI改动。真实8家复核全部查询完成、来源未429停止，报告数从66增至89，增加23份；仍未核验报告完整性、可比性或金额真实性。

已用同一缓存启动626家公司季度公告发现：`radar interim-discover --input tmp/quarter-discovery-20261017/scope.json --output tmp/quarter-discovery-20261017/scope-result.json --as-of 2026-10-17 --cache-dir tmp/quarter-discovery-20261017/cache`。本步不下载季度PDF、不自动审核、不发布，不启用周期更新；逐请求原始缓存可在中断后由同一命令复用。日志`tmp/jobs/quarter-discovery-scope-20261018.*.log`。全范围完成前不把8家结果推广为626家结果。

### 结构化财务来源只读探针

为减少PDF逐份适配，按当前已安装AkShare源码中的实际请求定义，对东方财富TCL科技指标接口取8期，另对2025年利润表与现金流量表各取1条，三次均HTTP200。原始响应、请求参数、抓取时间、SHA256留在`tmp/financial-source-probe-20261018/`；仅研究，不写库。指标接口为`datacenter.eastmoney.com/securities/api/data/get`的`RPT_F10_FINANCE_MAINFINADATA`，利润/现金流接口为`datacenter-web.eastmoney.com/api/data/v1/get`的`RPT_DMSK_FN_INCOME`、`RPT_DMSK_FN_CASHFLOW`。

指标接口有CURRENCY、NOTICE_DATE、UPDATE_DATE，但仅每股现金流及比率，没有已核验现金流总额；利润/现金流原始行缺明确币种字段。2025年指标归母4516782000元、扣非2896878481元；利润表归母4516782000、扣非2896877070；现金流表NETCASH_OPERATE为44021698000，归档年报现金流为44021698580。TOTALOPERATEREVE为营业总收入184210915000，不能等同此前年报表的营业收入184063390556。数值精度、字段口径及重述版本尚未完全对齐，不能自动将差异判为数据错误或选有利值；不采用每股现金流估算总额。此探针尚不足以建立正式筛选数据契约，未导入任何财务建议或审核事实。

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

# 年报发现及财务字段建议实施计划

日期2026-10-14。承接用户已确认共享财务证据、通用工作流及持续推进授权；不重新确认实现细节，不启用准入阈值。

## 范围与规则

扩展官方发现器支持annual_report模式，默认dividend保持兼容。年度报告关键词查询、完整报告与摘要/英文版区分、修订/更正风险保留，沿用精确公司身份、截点、分页、限流及留档规则。两种发现各用独立查询缓存目录。

新增年报解析模块与CLI `radar financial-extract`，读取留档PDF，先核对URL文件名、SHA/大小/PDF头。最多解析前20页的主要会计数据，不遍历数百页。复用25MiB文件限制，页数上限1000；超预算、扫描或不支持结构待核验，不放大下载限制。

支持明确证券代码、报告年度、人民币单位、连续三年表头的主要会计数据表。提取营业收入、归母净利润、扣非归母净利润、经营现金流建议，Decimal不浮点化；每条带年度、披露日、单位、来源、页码与摘录。标准无调整四列可对应当前/上年/前年；追溯调整或多列时只提取明确当前报告年度第一金额，比较年度不猜列映射，原始比较值保留待核验。负值保留，不以缺失当零；多章节同指标冲突不择优。

scope明确为reported_parent、consolidated等原文口径，不能自动改为company_ordinary。不推断无优先股/永续工具、不批准股类、不填coverage、profits或events，不从年度分红计划替代实际实施证据。financial_proposals全部requires_review=true、financial_verified=false。

工作流新增 `--annual-reports` 可选开关，默认不增加现有用户请求；开启后独立发现→留档→解析财务建议，审核包额外保留financial_documents/financial_proposals，规范证据及支付率研究仍只来自显式evidence。现有提取缓存仅适用分红，年报建议每轮从已核验原文提取；只解析前20页，网络仍复用留档。相关年度源失败明确计入partial，不影响其他公司和分红研究。无UI、数据库、自动任务或新依赖。

## 接口与执行分工

新增radar.stocks.financial_extract：`extract_annual_text(pages: list[str], *, symbol:str,source_url:str,disclosed_on:str,as_of:date)->dict[str,Any]`；`extract_archived_financials(archive_result:dict[str,Any],archive_dir:Path,as_of:date)->dict[str,Any]`，顶层items及financial_proposals，item含file_sha256/text_sha256/status/reasons及financial_proposals。

新增stock_robot.financial_documents CLI financial-extract。审核包build_review_packet新增可选关键字financial_extraction，保持旧调用兼容，绑定每项原文SHA并稳定标识。workflow run_workflow及CLI新增annual_reports bool，默认False；整体summary区分财务源和待核验。

测试先行，真实异常长江2025年度前页裁切保留调整前后表头。资源tests/resources/radar/cypc-600900-2025-annual-summary.txt，来源https://www.cypc.com.cn/cypc/attachDir/2026/05/2026051815345898336.pdf，采样2026-10-14，保留封面、第6页主要会计数据及第7页说明／季度数据的原始文本（PDF页索引0、5、6）；完整PDF留忽略tmp。新增资源在verify映射到财务模块及工作流测试。

财务模块及测试由独立实现代理执行；主会话接发现模式、审核包、工作流、CLI和映射。整阶段规格+质量双审查后复核。真实年报提取和实际annual模式查询验收，范围Ruff/Pyright/pytest，完整事项本地提交。所有建议仍不等于入池。

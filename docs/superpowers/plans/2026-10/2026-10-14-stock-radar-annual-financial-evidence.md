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

<a id="historical-2026-10-14-stock-radar-annual-financial-evidence-validation"></a>

## 年报发现与财务证据建议阶段验收

> 2026-11-24 文档整理：本节合并自 `2026-10-14-stock-radar-annual-financial-evidence-validation.md`，保留原历史记录；以下结果仅适用于所述日期和代码状态，不代表当前验收结果。

日期：2026-10-14。对应实施计划 `2026-10-14-stock-radar-annual-financial-evidence.md`。用户已授权持续推进，不逐小部分确认；本阶段不启用正式筛选，不发布成员。

### 实际改动

- 官方发现器增加annual_report模式，默认分红模式兼容；完整年报、摘要／英文／审议意见和修订／日期变更风险分开。工作流两种查询使用独立缓存。
- 增加财务前页解析、financial-extract CLI和dividend-workflow的可选--annual-reports。年报最大25MiB／1000页，有界读取25MiB+1字节后核验SHA、实际长度和PDF头，仅解析前20页同一已核验字节。
- 收入、归母净利润、扣非净利润和经营现金流输出建议，Decimal保留负值和单位；标准四列可对应三年，追溯多列仅当前报告年度。reported_parent不自动变为company_ordinary，不补股类或派息完整性。
- 审核资料包增加财务文档和稳定编号建议，沿用原文摘要、公司／来源／披露日绑定。建议永不自动写入规范profits、coverage或events。CLI、测试、验证映射、真实文本资源和使用说明一起收敛。

无新依赖、UI、数据库或自动任务。旧分红工作流默认不会额外请求年报。

### 真实来源及CLI验收

长江电力600900，截止2026-10-14，真实巨潮年度报告查询范围完成，发现四个年度（2022—2025）报告及一份2022年度披露日期变更风险公告。原文URL／披露日／校验和在忽略目录的annual-discovery.json和annual-archived.json保留；旧查询起点因此还包含目标三年之前的报告，不把历史比较期建议当目标年度入池结论。

执行真实入口：

```powershell
stock-robot radar dividend-workflow --input tmp/annual-evidence-acceptance/companies.json --workspace tmp/annual-financial-workflow --as-of 2026-10-14 --annual-reports
stock-robot radar financial-extract --archive-result tmp/annual-financial-workflow/annual-archived.json --archive-dir tmp/annual-financial-workflow/annual-pdf-archive --output tmp/annual-evidence-acceptance/financial-cli.json --as-of 2026-10-14
```

最终四份年报模板匹配，输出4／4／4／12条建议，共24条；前三份对应2023—2025目标年度，各提取4项当前数据。旧2022标准表还包含2020／2021比较期；报告与建议的财政年度均保留，不混作当前三年。日期变更资料仍pending_verification、无财务建议。financial_verified=false、requires_review=true、rule_enabled=false；匹配不证明普通股股东口径或持续盈利准入通过。

无显式证据时：1家公司研究完整0、待核验1。显式提供此前已核验长江规范证据（从现有证据资源按公司筛选，原始来源字段保留）后：研究完整1、待核验0；这是旧支付率研究证据兼容结果，不来自本阶段机器财务建议自动放行。工作流总体partial继续反映风险资料及不支持分红文本。

最终续跑6份分红PDF、5份年报／风险PDF均cached；官方查询缓存复用。财务解析每轮核验原文并解析前页，没有声称财务提取缓存复用。完整PDF、查询响应、产物与日志都保留忽略tmp，不提交。初轮整PDF产生说明页误判的pending基线已记录在本阶段工具输出，修复后真实前20页不混入季度数字。

### 审查与检查

独立规格、质量审查及修复复核通过。实际修复：说明／季度页误作主表；错误千分组被删逗号放大；缺失符号／游离负号／百分号／脚注被数字分词跳过；同页第二主表被忽略；首表缺收入时多表守卫遗漏其他金额行。异常行不猜值，多表整页待核验，正常章节标题和表头重复仍兼容。PDF stat后增长通过有界读取处理。

真实文本资源来自计划列出的官方公司PDF，采样2026-10-14，封面及PDF索引0／5／6原始文本包含追溯表头、说明与季度数字。`.gitattributes`仅对年报文本样本保留原始行尾空白，源码正常检查。初次年报模式测试先行确认缺少mode参数失败，再实现并验证原分红兼容。

最终命令：`.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`。17个暂存文件，Ruff通过、Pyright为0 errors／0 warnings／0 informations，272个相关pytest用例通过（30.31秒）。唯一测试临时目录已清理；补充本验收文档后仅做`git diff --cached --check`，复用同状态业务检查。没有运行全量回归或重复无关浏览器验收。

### 尚未覆盖

仅测试一家公司实际年报链路，不代表全市场覆盖率；其他年报结构、扫描件、超预算文档和前20页之外的数据可能待核验。所有股类、普通股利润调整、年度分红完整证明、治理及行业龙头证据仍未全自动核实。正式数值准入、最新TTM／独立季度机会指标和成员生命周期未由本阶段启用。

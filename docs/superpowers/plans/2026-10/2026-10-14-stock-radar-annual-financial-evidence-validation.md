# 年报发现与财务证据建议阶段验收

日期：2026-10-14。对应实施计划 `2026-10-14-stock-radar-annual-financial-evidence.md`。用户已授权持续推进，不逐小部分确认；本阶段不启用正式筛选，不发布成员。

## 实际改动

- 官方发现器增加annual_report模式，默认分红模式兼容；完整年报、摘要／英文／审议意见和修订／日期变更风险分开。工作流两种查询使用独立缓存。
- 增加财务前页解析、financial-extract CLI和dividend-workflow的可选--annual-reports。年报最大25MiB／1000页，有界读取25MiB+1字节后核验SHA、实际长度和PDF头，仅解析前20页同一已核验字节。
- 收入、归母净利润、扣非净利润和经营现金流输出建议，Decimal保留负值和单位；标准四列可对应三年，追溯多列仅当前报告年度。reported_parent不自动变为company_ordinary，不补股类或派息完整性。
- 审核资料包增加财务文档和稳定编号建议，沿用原文摘要、公司／来源／披露日绑定。建议永不自动写入规范profits、coverage或events。CLI、测试、验证映射、真实文本资源和使用说明一起收敛。

无新依赖、UI、数据库或自动任务。旧分红工作流默认不会额外请求年报。

## 真实来源及CLI验收

长江电力600900，截止2026-10-14，真实巨潮年度报告查询范围完成，发现四个年度（2022—2025）报告及一份2022年度披露日期变更风险公告。原文URL／披露日／校验和在忽略目录的annual-discovery.json和annual-archived.json保留；旧查询起点因此还包含目标三年之前的报告，不把历史比较期建议当目标年度入池结论。

执行真实入口：

```powershell
stock-robot radar dividend-workflow --input tmp/annual-evidence-acceptance/companies.json --workspace tmp/annual-financial-workflow --as-of 2026-10-14 --annual-reports
stock-robot radar financial-extract --archive-result tmp/annual-financial-workflow/annual-archived.json --archive-dir tmp/annual-financial-workflow/annual-pdf-archive --output tmp/annual-evidence-acceptance/financial-cli.json --as-of 2026-10-14
```

最终四份年报模板匹配，输出4／4／4／12条建议，共24条；前三份对应2023—2025目标年度，各提取4项当前数据。旧2022标准表还包含2020／2021比较期；报告与建议的财政年度均保留，不混作当前三年。日期变更资料仍pending_verification、无财务建议。financial_verified=false、requires_review=true、rule_enabled=false；匹配不证明普通股股东口径或持续盈利准入通过。

无显式证据时：1家公司研究完整0、待核验1。显式提供此前已核验长江规范证据（从现有证据资源按公司筛选，原始来源字段保留）后：研究完整1、待核验0；这是旧支付率研究证据兼容结果，不来自本阶段机器财务建议自动放行。工作流总体partial继续反映风险资料及不支持分红文本。

最终续跑6份分红PDF、5份年报／风险PDF均cached；官方查询缓存复用。财务解析每轮核验原文并解析前页，没有声称财务提取缓存复用。完整PDF、查询响应、产物与日志都保留忽略tmp，不提交。初轮整PDF产生说明页误判的pending基线已记录在本阶段工具输出，修复后真实前20页不混入季度数字。

## 审查与检查

独立规格、质量审查及修复复核通过。实际修复：说明／季度页误作主表；错误千分组被删逗号放大；缺失符号／游离负号／百分号／脚注被数字分词跳过；同页第二主表被忽略；首表缺收入时多表守卫遗漏其他金额行。异常行不猜值，多表整页待核验，正常章节标题和表头重复仍兼容。PDF stat后增长通过有界读取处理。

真实文本资源来自计划列出的官方公司PDF，采样2026-10-14，封面及PDF索引0／5／6原始文本包含追溯表头、说明与季度数字。`.gitattributes`仅对年报文本样本保留原始行尾空白，源码正常检查。初次年报模式测试先行确认缺少mode参数失败，再实现并验证原分红兼容。

最终命令：`.\scripts\verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`。17个暂存文件，Ruff通过、Pyright为0 errors／0 warnings／0 informations，272个相关pytest用例通过（30.31秒）。唯一测试临时目录已清理；补充本验收文档后仅做`git diff --cached --check`，复用同状态业务检查。没有运行全量回归或重复无关浏览器验收。

## 尚未覆盖

仅测试一家公司实际年报链路，不代表全市场覆盖率；其他年报结构、扫描件、超预算文档和前20页之外的数据可能待核验。所有股类、普通股利润调整、年度分红完整证明、治理及行业龙头证据仍未全自动核实。正式数值准入、最新TTM／独立季度机会指标和成员生命周期未由本阶段启用。

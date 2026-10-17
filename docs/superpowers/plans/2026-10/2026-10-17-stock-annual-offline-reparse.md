# 年报任务批量离线重解析

## 范围与授权

延续已确认的626家公司财务采集与待审流程。用户明确要求没有待确认事项就一直推进。本事项为已归档报告补通用重解析入口，不新增选股条件、网络来源、调度开关或页面。现有后台采集基于c4a79fb运行，已归档资料不应因后续解析器修复而反复下载或被旧partial断点永久锁住。

## 行为与接口

新增`radar stock-annual-reparse --source-workspace <原任务目录> --workspace <新研究目录> --as-of <同一研究日> [--db <已有库>]`。只读原任务manifest、逐公司result/checkpoint和PDF。调用现有归档核验、extract_archived_financials、build_review_packet和待审事实准备；不调用公告发现或下载。默认导出，显式db才导入已有公司且仍待审，禁止审核或发布。

原manifest版本为stock-annual-batch-v1，验证研究日、非空去重公司清单和input_sha256。目标清单绑定原manifest原始摘要、源绝对路径、研究日、重解析版本和financial_extract.py内容SHA256；解析器变化须使用新目标目录。源/目标互不嵌套、拒绝符号链接；数据库及日志不能位于源/目标目录内。复用原annual_batch的原子写、公司代码校验、摘要及PDF核验机制。

每次调用只处理已存在完整checkpoint的公司；原任务仍在进行而无checkpoint者为pending，不读写其未提交结果。读取result并验证checkpoint摘要、公司/名称和源清单归属及PDF绑定。损坏的源或目标记录拒绝提取/导入并报告，单公司故障不阻断其他公司；原目录保持不变。将提取建议限制在此前三个完整财政年度，所有currency_evidence等原文追踪保留。

结果保存到目标companies/<symbol>/result.json和checkpoint.json。复用时同时核验目标摘要、源result摘要及现存PDF，回调仍执行以恢复已提取但未导入的资料。回调失败保存进度后抛出可重试错误，不静默降级为成功。目标结果完整或部分均复用；不能自动覆盖既有待审资料。后续新增源checkpoint可在同一目标目录继续处理。

摘要区分processed、pending、completed、partial、failed、reused、companies_with_proposals、financial_proposals、import_failed及原始报告matched/pending数量，强制financial_verified=false、formal_screening_enabled=false、members=0。completed仅表示资料匹配完整，不表示选股合格。原查询范围、三年度缺失、歧义文档和失败归档仍导致partial，不因重解析而抹除。

## 实施与验证

1. A级新模块annual_reparse.py与独立测试，复用已有工具函数。覆盖源/目标绑定、活跃源pending、增量续跑、无网络、原/目标损坏、丢失或篡改PDF、跨公司拒绝、三年范围、解析器指纹变化、回调恢复与失败。
2. CLI复用年度采集的待审包导入工厂，保持原命令行为。补CLI注册、路径隔离、研究日及重复导入测试，在verify.ps1映射相关源码。
3. 一次合并规格/质量审查，运行相关测试与Ruff/Pyright，真实源目录离线批量导出及导入验证，不重复全量，也不为CLI数据改动触发UI布局或聊天验收。
4. 验证稳定后按完整事项本地提交。运行产物、PDF、数据库、日志留在忽略tmp。本轮可先处理现有已提交源资料，原采集结束后用同一命令继续剩余部分；不引入周期自动任务。

## 实测与检查

新增核心19项、CLI及原命令14项独立测试通过。相关解析/审核包/财务事实/CLI/验证映射共229项通过，另1项旧年度采集测试在os.replace摘要时遭遇WinError5；完整异常保留在`tmp/pytest/annual-reparse-related-38360-ce38f87b34834ccd894ad6e5da1624cd/`，文件仅Archive/Compressed属性，无ReadOnly，新唯一目录单独复核该用例通过。由此给共用_save补Windows5/32/33最多两次50/200ms短重试；非Windows权限错误和永久拒绝仍抛出、不改ACL、不改变旧目标，新增3项回归，受影响年度采集和重解析51项通过。所有改动Python文件Ruff、Pyright通过。未运行全量；合并规格/质量审查通过，存储小修由主会话diff和相关测试核对。

依赖映射补充后，检查脚本77项通过，3个精确集合断言需同步新增重解析测试目标；更新断言后仅复核该3项通过，没有重跑整套业务测试。失败记录保留在独立tmp/pytest目录，未清理首个存储异常产物。

真实调用（PowerShell，项目虚拟环境）：

```powershell
./.venv/Scripts/stock-robot.exe radar stock-annual-reparse --source-workspace tmp/annual-collect-live-20261017-c4a79fb --workspace tmp/annual-reparse-live-20261017 --as-of 2026-10-17 --db .stock_robot/radar.db
```

2026-10-17已启动有限后台流程：先对当前归档离线运行一次，等待原采集进程（同时核对PID与启动时间，避免等待复用PID）结束，再对同一目录续跑一次。脚本及状态位于忽略的`tmp/jobs/annual-reparse-follow-20261017.*`，非周期任务，自动更新设置仍关闭。初始实际处理98家公司时8家有108条待审金额建议，重解析及导入失败0；源采集同时推进，数据未审核、成员未发布。原文缓存实测2.36GiB时已向用户说明超过1GiB软提醒值，按已确认规则保留、不自动删除；新流程没有复制PDF。

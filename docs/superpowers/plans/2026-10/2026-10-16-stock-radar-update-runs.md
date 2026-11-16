# 股票池手动更新与候选发现任务

用户已确认手动更新、候选发现分开，自动更新默认关闭。本事项新增独立任务持久化与CLI，不把股票任务接入ETF `UpdateService`，也不注册定时任务。

## 状态与幂等

`StockUpdateStore`使用研究SQLite的独立表保存任务：任务类型只能为`refresh_members`或`discover_candidates`，状态为queued/running/completed/partial/failed/cancelled，阶段为queued/snapshot/collect/review/finalizing及终态。任务保存池范围、目标日、规则版本、起始成员池revision、输入快照、逐项结果、错误与尝试次数。

相同类型、排序后的池范围、目标日、规则版本只允许一个queued/running任务；返回同一任务，不重复采集。新任务先捕获成员或候选范围快照，任务运行过程中成员版本变化不修改该输入；完成结果是不可变批次，失败或部分失败不会改写上一份完整资料。任务可由worker以租约claim，过期租约最多两次回队、第三次失败；更新只能由持有worker_id的执行者完成。

`refresh_members`必须有至少一个当前有效或暂停成员，读取正式成员仓储的快照；退出成员不进入更新。`discover_candidates`只记录准备范围和来源阶段，不把候选或名录直接升级成员。两种任务均不自动审核、发布、暂停、退出或恢复。当前正式筛选关闭也不妨碍发现候选资料，但结果始终`formal_screening_enabled=false`。

## 执行与资源

执行器直接复用官方名录、原始日线及股本采集器，不重新实现网络协议。候选发现执行官方名录→内容寻址产物→共享研究候选导入；complete仅代表名录资料发现已完成，不等于资格通过。成员更新去重证券后采集日线、股本，财务/分红/风险后台复核仍标not_configured，因此整项保持partial，不能冒充完整成员复核。回调异常逐来源隔离并留摘要；自动入口不注册，始终关闭。

任务仓储没有网络；执行器通过已有适配器联网，沿用已确认请求预算。整个股票队列同一时刻只认领一项，证券去重后串行采集。租约90秒、每15秒续租，每次claim生成新token；同worker旧token及过期租约均拒写。候选导入在同一SQLite写事务中再次assert_owner，避免旧执行者补写；只保存待核验research_hint，不自动审核。产物按SHA256归档且损坏旧文件不覆盖；缓存/产物目录不得相互包含、包含SQLite/配置或经过符号链接。默认分页15，单页上限100。

## 接口与验收

新增 `radar.stocks.update_runs`仓储与`update_runner`执行器。CLI提供`stock-update-enqueue`、`stock-update-list`、`stock-update-run-one`，仅用于手动任务；run-one支持缓存/产物路径及已验证市场窗口文件。结果scope的summary为文字，结构化统计和来源保存在artifact引用及原文产物；已完成scope幂等且不可覆盖，过期恢复续跑跳过成功scope，已终结批次不可变。

测试覆盖：双池规范化、空成员拒绝、资料不授予资格、并发幂等/单执行者、租约与旧token、过期续跑/成功scope不可改、事务内输入快照、部分失败、guard拒写、产物hash/损坏/路径、分页、CLI、自动关闭与验证映射。真实CLI执行一次名录候选发现，核对三源状态、研究库待核验资料与成员未发布；重复入队只复用活动任务，缓存复用不伪造采集日期。完成双审及Changed范围门禁后本地提交。

本轮另修复上一笔成员管理两项真实问题：请求重放先于日期新鲜度/禁用配置校验；暂停/退出结果不再误报formal_screening_enabled=true，并把首次评估新鲜度检查置于写事务。保持当前正式筛选关闭，不增加任何阈值或发布授权。

## 实施与真实验收

2026-10-16真实CLI：同请求两次入队都为aa243696efb844d88386efb4664b4fd5；三源均下载成功，原始行主板1702、科创618（排除CDR1）、深市2906，合并普通A股候选5225。首次执行追加5225条待核验线索、公司新增0，批次0869668e34f28386dd99d411c3fb20599a2917f0123c9ba3269af36dd30bcd00；正式筛选false、发布0。

第二次新批次执行全部来源cached，source SHA和fetched_at不变；没有新增候选证据。两正式池成员查询均total0/revision0。产物a34991e8e1fa629fce9053ad46d45cea9240d9881416f0ec6fd88cae8cf4b9aa与缓存产物均留tmp；缓存产物13185002字节，实际低于64MiB上限。SZSE下载文件的已知默认样式警告仅为openpyxl提示，没有隐藏或修改依赖。

集成首轮4项失败为summary接口不一致（执行器结构化对象、仓储要求文本），已改summary文字/统计置artifact，随后98项通过。补齐全来源失败与断点恢复后106项通过；最终新增旧partial续跑全失败按最新scope聚合、3/10秒退避回归将由暂存门禁覆盖。规格与质量独立双审及复审均PASS：全源失败不写空批次；成功scope恢复前重验产物；历史partial不能掩盖新失败。

最终范围验证：`verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`覆盖22个相关测试文件，Ruff通过、Pyright 0错误；pytest 511 passed/1 skipped/1 failed（105.40秒），跳过为Windows符号链接权限。唯一失败为既有财务CLI样本固定2026-10-15截点却使用当前2026-10-16时钟审核，系统正确拒绝历史时点采用未来审核。固定该测试文件所有实例的采集/审核时钟后，单文件10 passed（2.37秒）、Ruff及Pyright通过；其余已通过范围代码未变并复用结果，组合覆盖512项通过、1项跳过，未重复整轮门禁。失败检查目录保留用于追溯。

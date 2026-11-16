# 评估留档与成员草案验收记录

日期2026-10-15。承接d37eb24，工作树codex/stock-radar-pools。本阶段是已确认流程的底层/CLI实现，无网页或会话改动，无新增依赖。

## 实际交付

- StockEvaluationStore：同SQLite独立feature版本和表；保存完整公司/证据原文/审核身份及时间、计算报告、规则版本和市场上下文。规范内容SHA256幂等，captured_at不进入身份，旧档案不改写。保存事务内核对revision、完整来源、全范围名录；部分失败独立留档。动态检查日期、规则、来源和90天审核失效。
- StockMembershipDraftStore：新增关注草案、请求幂等、整批原子校验、blocked原因、版本化撤销及追加事件。仅支持add，无正式成员写入或发布入口；来源变化后旧草案保留并动态失效。
- 五个CLI：stock-evaluation-capture/list、stock-member-draft-create/list/cancel。capture仅服务端重新计算；外部JSON不能批准资格。保护DB/WAL/SHM/journal、市场窗口、草案输入及正在读取的股票池配置目录。
- 原研究命令共享市场窗口解析，行为保持原用途；检查脚本增加明确映射，使用说明列明complete/freshness均不等于资格。

## 验证及审查

测试先行：草案模块不存在时收集失败；新CLI尚未注册时3失败5通过；实现后补充并发重放、部分失败依据拒绝、未知schema版本、错误路径断言及池配置保护测试。

执行 `scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv`，Ruff通过、Pyright 0错误、PowerShell语法检查通过，pytest **356项通过（50.85秒）**；验证脚本成功清理本次目录。改动无未映射文件，覆盖：

- 评估档案25项、成员草案21项、归档CLI10项。
- 原共同引擎80项、共同CLI16项、风格引擎53项及风格CLI6项。
- 共享研究存储、verify映射及CLI相关测试。

规格与质量审查最终均PASS，覆盖核心、CLI及使用说明。质量审查发现草案导出可覆盖池配置YAML，已修复为resolve后拒绝输出进入pool-dir，create/list两项回归验证原YAML不变，定向复审通过。没有遗留审查问题。未运行全量回归，未重复无本阶段改动的真实浏览器/会话验收。

## 真实685库留档

使用共享venv正式stock-robot.exe入口及当前工作树PYTHONPATH，对 `tmp/stock-research-acceptance/final.db` 两次执行stock-evaluation-capture，截点2026-10-15。输出archive-capture-first.json与archive-capture-second.json。

两次ID均 `877f567be450421105372c21bee84acefaae2e45b4a72c6670742e5a8483a4a0`，首次created_at均2026-10-15T05:54:51.355044+00:00。仅1份档案，status=complete、freshness.current=true、companies_total=685。原数据库统计仍685公司、772证据、0审核、3批次。formal_screening_enabled=false、members_published=0；所有候选资格仍待核验。complete仅表示计算完整，不能用于声称通过选股或正式入池。

## 正式命令合成草案验收

另用 `tmp/stock-research-acceptance/draft-executable-probe/synthetic.db`，公司名称及署名明确为合成测试，不是用户首批关注选择。通过正式exe创建档案、保存草案、更新证据再查询草案：初始status=blocked、revision=1、evaluation_current=true；资料更新后evaluation_current=false，原因source_revision_changed；档案及草案保留，publication_enabled=false、members_published=0。撤销及旧revision拒绝由真实SQLite集成测试覆盖。

真实库/合成库/输出报告与失败诊断保留忽略目录tmp，不提交。正式成员发布、暂停/退出/恢复、风险生命周期、后台采集任务及管理UI仍未完成；研究档案不替代生产行情/交易日历版本，也不重建历史时点审核。尚未验证全市场正式筛选通过数量、完整采集耗时与内存峰值。本地提交不隐含推送、合并或发布。
